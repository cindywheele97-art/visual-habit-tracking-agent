import AppKit
import CoreVideo
import Foundation
import GlimpseShellLib

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    private let overlay = OverlayController()
    private let capture = CaptureEngine()
    private let diffGate = DiffGate()
    private var ipc: IPCClient!
    private var selector: RegionSelector?
    private var seq = 0
    private let regionId = "region-1"
    private let isoFormatter = ISO8601DateFormatter()
    private var watching = false
    private var lastEmptyOcr: Date?

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.title = "👁"
        let menu = NSMenu()
        menu.addItem(
            NSMenuItem(title: "Select Region & Watch", action: #selector(selectRegion), keyEquivalent: "r")
        )
        menu.addItem(NSMenuItem(title: "Stop Watching", action: #selector(stopWatching), keyEquivalent: "."))
        menu.addItem(.separator())
        menu.addItem(
            NSMenuItem(title: "Quit Glimpse", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        )
        menu.items.forEach { $0.target = self }
        statusItem.menu = menu

        let socketPath = ("~/.glimpse/glimpse.sock" as NSString).expandingTildeInPath
        ipc = IPCClient(path: socketPath)
        ipc.onConnect = { [weak self] in
            self?.ipc.send(HelloMsg(shellVersion: "0.1.0"))
        }
        ipc.onMessage = { [weak self] message in
            self?.handle(message)
        }
        ipc.start()

        overlay.model.onCopy = { [weak self] suggestionId in
            guard let self else { return }
            self.ipc.send(CopiedMsg(suggestionId: suggestionId, regionId: self.regionId))
        }
        overlay.show()

        // Region-dead watchdog (spec §5): frames keep changing but OCR finds no
        // text for 2 minutes → the watched window probably closed or moved.
        // A static-but-alive chat sends no frames at all, so it never trips this.
        Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
            guard let self, self.watching, let emptySince = self.lastEmptyOcr else { return }
            if Date().timeIntervalSince(emptySince) > 120 {
                self.overlay.setStatus("degraded", detail: "region looks dead — reselect?")
            }
        }

        if let saved = RegionStore.load() {
            startWatching(region: saved)
        }
    }

    @objc private func selectRegion() {
        selector = RegionSelector { [weak self] region in
            RegionStore.save(region)
            self?.startWatching(region: region)
            self?.selector = nil
        }
        selector?.begin()
    }

    @objc private func stopWatching() {
        watching = false
        Task { await capture.stop() }
        overlay.setStatus("stopped", detail: "")
    }

    private func startWatching(region: CGRect) {
        Task {
            do {
                try await capture.start(region: region) { [weak self] pixelBuffer in
                    self?.processFrame(pixelBuffer)
                }
                self.watching = true
                self.lastEmptyOcr = nil
                self.overlay.setStatus("watching", detail: "")
            } catch {
                self.overlay.setStatus("error", detail: "capture: \(error.localizedDescription)")
            }
        }
    }

    /// Runs on the capture queue (not main): diff gate → OCR → IPC.
    private func processFrame(_ pixelBuffer: CVPixelBuffer) {
        let samples = Diff.sample(pixelBuffer)
        guard diffGate.isChanged(samples) else { return }
        guard let image = ImageUtil.cgImage(from: pixelBuffer) else { return }
        let blocks = (try? OCR.recognize(image)) ?? []
        if blocks.isEmpty {
            DispatchQueue.main.async {
                if self.lastEmptyOcr == nil { self.lastEmptyOcr = Date() }
            }
            return
        }
        DispatchQueue.main.async { self.lastEmptyOcr = nil }
        seq += 1
        let message = OcrMsg(
            seq: seq, ts: isoFormatter.string(from: Date()),
            regionId: regionId, blocks: blocks
        )
        ipc.send(message, ackSeq: message.seq)
    }

    private func handle(_ message: BrainMessage) {
        switch message {
        case .ack:
            break  // IPCClient already cleared its resend slot
        case .suggestions(let msg):
            overlay.update(items: msg.items, stale: msg.stale)
        case .status(let msg):
            overlay.setStatus(msg.state, detail: msg.detail)
        }
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)  // menu-bar app: no Dock icon
app.run()
