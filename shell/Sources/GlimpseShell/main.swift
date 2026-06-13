import AppKit
import CoreVideo
import Foundation
import GlimpseShellLib

/// Watch flags confined to the main run loop: written via main-queue hops from
/// the capture queue, read by the watchdog Timer on main. All access is on the
/// main thread, which is what makes the @unchecked Sendable conformance safe —
/// holding them here lets the background→main hops capture this small object
/// instead of the whole (non-Sendable) AppDelegate.
private final class WatchFlags: @unchecked Sendable {
    var watching = false
    var lastEmptyOcr: Date?
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    private let overlay = OverlayController()
    private let capture = CaptureEngine()
    private let diffGate = DiffGate()
    private var ipc: IPCClient!
    private var selector: RegionSelector?
    private var clickSensor: ClickSensor?
    // Capture-queue-confined: processFrame is the only reader/writer after
    // init. Do not read seq from main without hopping to the capture queue.
    private var seq = 0
    private let regionId = "region-1"
    private let isoFormatter = ISO8601DateFormatter()
    // Main-run-loop-confined watch flags (see WatchFlags).
    private let flags = WatchFlags()
    private var sender: Sender!
    private var calibrator: InputBoxCalibrator?
    private var countdownTimer: Timer?
    private var escMonitors: [Any] = []
    private var autoSendEnabled: Bool {
        get { UserDefaults.standard.bool(forKey: "autoSendEnabled") }
        set { UserDefaults.standard.set(newValue, forKey: "autoSendEnabled") }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.title = "👁"
        let menu = NSMenu()
        menu.addItem(
            NSMenuItem(title: "Select Region & Watch", action: #selector(selectRegion), keyEquivalent: "r")
        )
        menu.addItem(NSMenuItem(title: "Stop Watching", action: #selector(stopWatching), keyEquivalent: "."))
        menu.addItem(
            NSMenuItem(title: "Today's Interests", action: #selector(summarize), keyEquivalent: "t")
        )
        menu.addItem(
            NSMenuItem(title: "设置输入框位置", action: #selector(calibrateInputBox), keyEquivalent: "i")
        )
        let autoSendItem = NSMenuItem(
            title: "自动发送", action: #selector(toggleAutoSend(_:)), keyEquivalent: ""
        )
        autoSendItem.state = autoSendEnabled ? .on : .off
        menu.addItem(autoSendItem)
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

        let sensor = ClickSensor(allowlist: AppAllowlist(path: AppAllowlist.defaultPath)) { [weak self] msg in
            self?.ipc.send(msg)
        }
        sensor.onPermissionNeeded = { [weak self] in
            self?.overlay.setStatus("error", detail: "Accessibility needed for click tracking")
        }
        sensor.start()
        clickSensor = sensor

        overlay.model.onCopy = { [weak self] suggestionId in
            guard let self else { return }
            self.ipc.send(CopiedMsg(suggestionId: suggestionId, regionId: self.regionId))
        }

        let sendAllowlist = AppAllowlist(path: AppAllowlist.defaultPath)
        sender = Sender(
            synthetic: CGEventSyntheticInput(),
            isFrontmostAllowed: {
                sendAllowlist.isAllowed(NSWorkspace.shared.frontmostApplication?.bundleIdentifier)
            },
            inputBoxPoint: { InputBoxStore.load() },
            accessibilityTrusted: { AXIsProcessTrusted() },
            setPasteboard: { text in
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(text, forType: .string)
            },
            presentRefusal: { [weak self] reason in
                self?.overlay.setStatus("degraded", detail: reason.message)
            },
            presentCountdown: { [weak self] countdown in
                self?.driveCountdown(countdown)
            },
            emitReplied: { [weak self] id, mode in
                guard let self else { return }
                self.ipc.send(RepliedMsg(suggestionId: id, regionId: self.regionId, mode: mode))
            }
        )

        overlay.model.onAct = { [weak self] id, text in
            guard let self else { return }
            self.sender.handle(
                suggestionId: id, text: text,
                autoSendOn: self.autoSendEnabled, stale: self.overlay.model.stale
            )
        }
        overlay.setAutoSend(autoSendEnabled)

        overlay.show()

        // Region-dead watchdog (spec §5): frames keep changing but OCR finds no
        // text for 2 minutes → the watched window probably closed or moved.
        // A static-but-alive chat sends no frames at all, so it never trips this.
        Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
            guard let self, self.flags.watching, let emptySince = self.flags.lastEmptyOcr
            else { return }
            if Date().timeIntervalSince(emptySince) > 120 {
                self.overlay.setStatus("degraded", detail: "region looks dead — reselect?")
            }
        }

        if let saved = RegionStore.load() {
            startWatching(region: saved)
        }
    }

    @objc private func summarize() {
        ipc.send(SummarizeRequest())
    }

    @objc private func selectRegion() {
        selector = RegionSelector { [weak self] region in
            RegionStore.save(region)
            self?.startWatching(region: region)
            self?.selector = nil
        }
        selector?.begin()
    }

    @objc private func calibrateInputBox() {
        calibrator = InputBoxCalibrator { [weak self] point in
            InputBoxStore.save(point)
            self?.calibrator = nil
            self?.overlay.setStatus("watching", detail: "输入框已设置")
        }
        calibrator?.begin()
    }

    @objc private func toggleAutoSend(_ item: NSMenuItem) {
        autoSendEnabled.toggle()
        item.state = autoSendEnabled ? .on : .off
        overlay.setAutoSend(autoSendEnabled)
    }

    /// Ticks the countdown once per second, mirrors it into the overlay banner,
    /// and lets Esc cancel it. Re-check of the frontmost app happens inside the
    /// countdown's completion (Sender.finalizeSend).
    private func driveCountdown(_ countdown: Countdown) {
        // A superseding auto-send re-enters here while the previous countdown's
        // monitors/timer are still live; tear them down first so a leaked local
        // Esc monitor can't swallow Esc app-wide for the process lifetime.
        tearDownCountdown()
        overlay.showCountdown(remaining: countdown.remaining)
        let local = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            if event.keyCode == 53 {
                self?.sender.cancelPendingSend()
                self?.overlay.hideCountdown()  // crisp abort: don't wait for the next tick
                return nil
            }
            return event
        }
        let global = NSEvent.addGlobalMonitorForEvents(matching: .keyDown) { [weak self] event in
            if event.keyCode == 53 {
                self?.sender.cancelPendingSend()
                self?.overlay.hideCountdown()
            }
        }
        escMonitors = [local, global].compactMap { $0 }

        countdownTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] timer in
            guard let self else { timer.invalidate(); return }
            countdown.tick()
            self.overlay.showCountdown(remaining: max(countdown.remaining, 0))
            if countdown.isFinished {
                self.tearDownCountdown()
                self.overlay.hideCountdown()
            }
        }
    }

    /// Invalidate the tick timer and remove the Esc monitors. Idempotent — safe
    /// to call at countdown completion and again on the next drive.
    private func tearDownCountdown() {
        countdownTimer?.invalidate()
        countdownTimer = nil
        escMonitors.forEach { NSEvent.removeMonitor($0) }
        escMonitors = []
    }

    @objc private func stopWatching() {
        flags.watching = false
        Task { await capture.stop() }
        overlay.setStatus("stopped", detail: "")
    }

    private func startWatching(region: CGRect) {
        let flags = self.flags
        Task {
            do {
                try await capture.start(region: region) { [weak self] pixelBuffer in
                    self?.processFrame(pixelBuffer)
                }
                // Task{} runs on the cooperative pool, not main — hop before
                // touching the main-confined flags the watchdog reads.
                DispatchQueue.main.async {
                    flags.watching = true
                    flags.lastEmptyOcr = nil
                }
                self.overlay.setStatus("watching", detail: "")
            } catch {
                self.overlay.setStatus("error", detail: "capture: \(error.localizedDescription)")
            }
        }
    }

    /// Runs on the capture queue (not main): diff gate → OCR → IPC.
    private func processFrame(_ pixelBuffer: CVPixelBuffer) {
        let flags = self.flags
        let samples = Diff.sample(pixelBuffer)
        guard diffGate.isChanged(samples) else { return }
        guard let image = ImageUtil.cgImage(from: pixelBuffer) else { return }
        let blocks = (try? OCR.recognize(image)) ?? []
        if blocks.isEmpty {
            DispatchQueue.main.async {
                if flags.lastEmptyOcr == nil { flags.lastEmptyOcr = Date() }
            }
            return
        }
        DispatchQueue.main.async { flags.lastEmptyOcr = nil }
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
        case .summary(let msg):
            overlay.showSummary(msg.text)
        }
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)  // menu-bar app: no Dock icon
app.run()
