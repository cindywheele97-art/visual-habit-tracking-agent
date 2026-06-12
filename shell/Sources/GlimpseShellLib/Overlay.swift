import AppKit
import SwiftUI

public final class OverlayModel: ObservableObject {
    @Published public var items: [SuggestionItem] = []
    @Published public var stale = false
    @Published public var status = "connecting"
    @Published public var detail = ""
    public var onCopy: ((String) -> Void)?

    public init() {}
}

/// Non-activating floating panel: visible above the chat app, never steals focus.
public final class OverlayController {
    public let model = OverlayModel()
    private let panel: NSPanel

    public init() {
        panel = NSPanel(
            contentRect: NSRect(x: 80, y: 120, width: 340, height: 300),
            styleMask: [.nonactivatingPanel, .titled, .closable, .resizable],
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

    public func update(items: [SuggestionItem], stale: Bool) {
        model.items = items
        model.stale = stale
    }

    public func setStatus(_ state: String, detail: String) {
        model.status = state
        model.detail = detail
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
            if model.items.isEmpty {
                Text("等待新消息…").font(.callout).foregroundColor(.secondary)
            }
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
            Spacer()
        }
        .padding(10)
        .frame(minWidth: 320, minHeight: 200)
    }
}
