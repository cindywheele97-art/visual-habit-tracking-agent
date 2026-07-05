import Darwin
import Foundation

/// NDJSON client over a Unix domain socket. Auto-reconnects with 1s backoff,
/// re-sends the last unacknowledged OCR payload after reconnecting (spec §5).
public final class IPCClient {
    public var onMessage: ((BrainMessage) -> Void)?
    public var onConnect: (() -> Void)?

    private let path: String
    private let queue = DispatchQueue(label: "glimpse.ipc")
    private var fd: Int32 = -1
    private var readSource: DispatchSourceRead?
    private var lineBuffer = LineBuffer()
    private var spool = OutboundSpool()

    public init(path: String) {
        self.path = path
    }

    public func start() {
        queue.async { self.connect() }
    }

    /// Send a message. For OCR payloads pass `ackSeq` so the message is retained
    /// and re-sent if the brain restarts before acknowledging it.
    public func send<T: Codable>(_ msg: T, ackSeq: Int? = nil) {
        queue.async {
            guard let data = try? Wire.encodeLine(msg) else { return }
            if let seq = ackSeq {
                self.spool.setUnacked(data, seq: seq)
            } else if self.fd < 0 {
                let before = self.spool.droppedCount
                self.spool.enqueue(data)
                if self.spool.droppedCount > before {
                    // Fail loud: a full offline queue drops the OLDEST message,
                    // which can be a RepliedMsg auto-send audit record. Never
                    // let that vanish silently.
                    NSLog("IPC outbound queue full — dropped \(self.spool.droppedCount) oldest message(s) during brain outage")
                }
                return
            }
            self.writeData(data)
        }
    }

    // MARK: - Connection lifecycle (all on `queue`)

    private func connect() {
        guard fd < 0 else { return }
        let newFd = Self.dial(path)
        guard newFd >= 0 else {
            queue.asyncAfter(deadline: .now() + 1.0) { [weak self] in self?.connect() }
            return
        }
        fd = newFd
        lineBuffer = LineBuffer()
        startReading()
        DispatchQueue.main.async { self.onConnect?() }
        for pending in spool.drainOnConnect() {
            writeData(pending)
        }
    }

    private func startReading() {
        let source = DispatchSource.makeReadSource(fileDescriptor: fd, queue: queue)
        source.setEventHandler { [weak self] in self?.readAvailable() }
        readSource = source
        source.resume()
    }

    private func readAvailable() {
        var buf = [UInt8](repeating: 0, count: 4096)
        let n = read(fd, &buf, buf.count)
        guard n > 0 else {
            disconnect()
            return
        }
        for line in lineBuffer.feed(Data(buf[0..<n])) {
            guard let msg = Wire.decodeBrainMessage(line) else { continue }
            if case .ack(let ack) = msg {
                spool.onAck(ack.seq)
            }
            DispatchQueue.main.async { self.onMessage?(msg) }
        }
    }

    private func disconnect() {
        readSource?.cancel()
        readSource = nil
        if fd >= 0 {
            close(fd)
            fd = -1
        }
        queue.asyncAfter(deadline: .now() + 1.0) { [weak self] in self?.connect() }
    }

    private func writeData(_ data: Data) {
        guard fd >= 0 else { return }
        // Local UDS + <4KB lines: write is atomic w.r.t. the kernel buffer;
        // a short write is not expected here.
        let result = data.withUnsafeBytes { rawBuffer -> Int in
            guard let baseAddress = rawBuffer.baseAddress else { return -1 }
            return Darwin.write(fd, baseAddress, data.count)
        }
        if result < 0 {
            disconnect()
        }
    }

    private static func dial(_ path: String) -> Int32 {
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { return -1 }
        // Without SO_NOSIGPIPE, writing after the brain dies raises SIGPIPE
        // and kills the whole shell process instead of returning EPIPE.
        var one: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, socklen_t(MemoryLayout<Int32>.size))
        var addr = sockaddr_un()
        addr.sun_family = sa_family_t(AF_UNIX)
        let pathBytes = path.utf8CString
        let capacity = MemoryLayout.size(ofValue: addr.sun_path)
        guard pathBytes.count <= capacity else {
            close(fd)
            return -1
        }
        withUnsafeMutableBytes(of: &addr.sun_path) { dst in
            pathBytes.withUnsafeBytes { src in
                dst.copyMemory(from: UnsafeRawBufferPointer(rebasing: src[0..<pathBytes.count]))
            }
        }
        let len = socklen_t(MemoryLayout<sockaddr_un>.size)
        let result = withUnsafePointer(to: &addr) { ptr in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sa in
                Darwin.connect(fd, sa, len)
            }
        }
        guard result == 0 else {
            close(fd)
            return -1
        }
        return fd
    }
}
