import AppKit
import SwiftUI

public final class OverlayModel: ObservableObject {
    @Published public var items: [SuggestionItem] = []
    @Published public var stale = false
    @Published public var status = "connecting"
    @Published public var detail = ""
    @Published public var summary = ""
    public var onCopy: ((String) -> Void)?

    public init() {}
}

/// Non-activating floating panel: visible above the chat app, never steals focus.
public final class OverlayController {
    public let model = OverlayModel()
    private let panel: NSPanel

    public init() {
        // No .closable: an accidentally closed panel has no reopen path until
        // a menu item exists; an always-on assist must not be dismissable.
        panel = NSPanel(
            contentRect: NSRect(x: 80, y: 120, width: 340, height: 300),
            styleMask: [.nonactivatingPanel, .titled, .resizable],
            backing: .buffered, defer: false
        )
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.isFloatingPanel = true
        panel.hidesOnDeactivate = false
        panel.title = "Glimpse"
        panel.contentView = NSHostingView(rootView: OverlayView(model: model))
    }

    public func show() {
        panel.orderFrontRegardless()
    }

    /// Safe to call from any thread: @Published mutation hops to main inside.
    public func update(items: [SuggestionItem], stale: Bool) {
        DispatchQueue.main.async {
            self.model.items = items
            self.model.stale = stale
        }
    }

    /// Safe to call from any thread: @Published mutation hops to main inside.
    public func showSummary(_ text: String) {
        DispatchQueue.main.async {
            self.model.summary = text
        }
    }

    /// Safe to call from any thread: @Published mutation hops to main inside.
    public func setStatus(_ state: String, detail: String) {
        DispatchQueue.main.async {
            self.model.status = state
            self.model.detail = detail
        }
    }
}

struct OverlayView: View {
    @ObservedObject var model: OverlayModel

    private var dotColor: Color {
        switch model.status {
        case "watching": return .green
        case "thinking": return .blue
        case "degraded": return .orange
        default: return .red
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                Circle().fill(dotColor).frame(width: 9, height: 9)
                Text(model.status).font(.caption).foregroundColor(.secondary)
                if !model.detail.isEmpty {
                    Text("— \(model.detail)").font(.caption).foregroundColor(.secondary)
                }
                if model.stale {
                    Text("stale").font(.caption2).padding(2)
                        .background(Color.orange.opacity(0.3)).cornerRadius(3)
                }
                Spacer()
            }
            if !model.summary.isEmpty {
                Text("今日关注").font(.caption).bold().foregroundColor(.secondary)
                Text(model.summary)
                    .font(.system(size: 13))
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(8)
                    .background(Color.blue.opacity(0.08))
                    .cornerRadius(6)
            }
            if model.items.isEmpty {
                Text("等待新消息…").font(.callout).foregroundColor(.secondary)
            }
            ScrollView {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(model.items) { item in
                        HStack(alignment: .top, spacing: 8) {
                            Text(item.text)
                                .font(.system(size: 13))
                                .textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
                            Button("复制") {
                                NSPasteboard.general.clearContents()
                                NSPasteboard.general.setString(item.text, forType: .string)
                                model.onCopy?(item.id)
                            }
                        }
                        .padding(8)
                        .background(Color.gray.opacity(0.12))
                        .cornerRadius(6)
                    }
                }
            }
            Spacer()
        }
        .padding(10)
        .frame(minWidth: 320, minHeight: 200)
    }
}
