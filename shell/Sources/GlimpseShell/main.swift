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
    private var attention: AttentionSensor?
    // Capture-queue-confined: processFrame is the only reader/writer after
    // init. Do not read seq from main without hopping to the capture queue.
    private var seq = 0
    private let regionId = "region-1"
    private let isoFormatter = ISO8601DateFormatter()
    // Main-run-loop-confined watch flags (see WatchFlags).
    private let flags = WatchFlags()
    private var sender: Sender!
    private var calibrator: InputBoxCalibrator?
    private var contactSelector: RegionSelector?
    private let contactReader = ContactReader()
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
        menu.addItem(
            NSMenuItem(title: "设置联系人区域", action: #selector(calibrateContactRegion), keyEquivalent: "")
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

        // Dwell sensor (P7.2): closed attention intervals on allowlisted apps
        // become DwellMsg events — the flywheel's primary implicit-attention
        // signal. onDwell fires on the main run loop (Timer).
        let dwellSensor = AttentionSensor(
            allowlist: AppAllowlist(path: AppAllowlist.defaultPath)
        ) { [weak self] interval in
            guard let self else { return }
            self.ipc.send(
                DwellMsg(
                    app: interval.app,
                    windowTitle: interval.title,
                    url: "",  // P7.4 browser-extension tier fills this
                    startTs: self.isoFormatter.string(
                        from: Date(timeIntervalSince1970: interval.start)),
                    endTs: self.isoFormatter.string(
                        from: Date(timeIntervalSince1970: interval.end)),
                    seconds: interval.seconds
                )
            )
        }
        dwellSensor.start()
        attention = dwellSensor

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
            readContact: { [weak self] completion in
                guard let self else { return completion("") }
                // Fresh capture+OCR, not the 3s poll cache: the cache can lag a
                // chat switch by most of the countdown.
                self.contactReader.readFresh(completion: completion)
            },
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
                if mode != "fill" {
                    // "sent"/"cancelled" = an armed send RESOLVED — only now is
                    // it safe to drop the Esc monitors and banner. The countdown
                    // hitting 0 is not resolution: contact verification may
                    // still be in flight, and Esc must be able to abort it.
                    self.tearDownCountdown()
                    self.overlay.hideCountdown()
                }
            }
        )

        overlay.model.onAct = { [weak self] id, text in
            guard let self else { return }
            self.sender.handle(
                suggestionId: id, text: text,
                autoSendOn: self.autoSendEnabled, stale: self.overlay.model.stale
            )
        }
        overlay.model.onFeedback = { [weak self] id, verdict, note in
            guard let self else { return }
            self.ipc.send(FeedbackMsg(suggestionId: id, regionId: self.regionId, verdict: verdict, note: note))
        }
        overlay.setAutoSend(autoSendEnabled)

        contactReader.start()
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
            self?.selector = nil
            guard let region else {
                self?.overlay.setStatus("watching", detail: "已取消")
                return
            }
            RegionStore.save(region)
            self?.startWatching(region: region)
        }
        selector?.begin()
    }

    @objc private func calibrateContactRegion() {
        contactSelector = RegionSelector { [weak self] rect in
            self?.contactSelector = nil
            guard let rect else {
                self?.overlay.setStatus("watching", detail: "已取消")
                return
            }
            ContactRegionStore.save(rect)
            self?.overlay.setStatus("watching", detail: "联系人区域已设置")
        }
        contactSelector?.begin()
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
        // Kill-switch: turning auto-send off must abort any countdown already
        // in flight, not just affect future sends.
        if !autoSendEnabled { sender.cancelPendingSend() }
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
                self?.tearDownCountdown()  // crisp abort: stop the timer/monitors now
                self?.overlay.hideCountdown()
                return nil
            }
            return event
        }
        let global = NSEvent.addGlobalMonitorForEvents(matching: .keyDown) { [weak self] event in
            if event.keyCode == 53 {
                self?.sender.cancelPendingSend()
                self?.tearDownCountdown()
                self?.overlay.hideCountdown()
            }
        }
        escMonitors = [local, global].compactMap { $0 }

        countdownTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] timer in
            guard let self else { timer.invalidate(); return }
            countdown.tick()
            self.overlay.showCountdown(remaining: max(countdown.remaining, 0))
            if countdown.isFinished {
                // Ticking is over, but the send may still be verifying the
                // contact. Stop the timer only — the Esc monitors and banner
                // stay until emitReplied reports sent/cancelled.
                self.countdownTimer?.invalidate()
                self.countdownTimer = nil
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
        let contact = contactReader.current
        let imageB64 = ImageUtil.downscaledJPEGBase64(image) ?? ""
        let message = OcrMsg(
            seq: seq, ts: isoFormatter.string(from: Date()),
            regionId: regionId, blocks: blocks, contact: contact,
            image: imageB64
        )
        overlay.showContact(contact)
        ipc.send(message, ackSeq: message.seq)
    }

    private func handle(_ message: BrainMessage) {
        switch message {
        case .ack:
            break  // IPCClient already cleared its resend slot
        case .suggestions(let msg):
            sender.noteSuggestionsUpdate(stale: msg.stale)
            overlay.update(items: msg.items, stale: msg.stale)
        case .status(let msg):
            overlay.setStatus(msg.state, detail: msg.detail)
        case .summary(let msg):
            overlay.showSummary(msg.text)
        case .advisory(let msg):
            overlay.showAdvisory(msg.text)
        }
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)  // menu-bar app: no Dock icon
app.run()
