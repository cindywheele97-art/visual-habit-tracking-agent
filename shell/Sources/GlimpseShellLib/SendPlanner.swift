/// Why a send was refused; carries the message surfaced in the overlay status.
public enum RefuseReason: Equatable {
    case wrongApp
    case notCalibrated
    case accessibilityUntrusted

    public var message: String {
        switch self {
        case .wrongApp: return "切换到微信再发送"
        case .notCalibrated: return "先设置输入框位置"
        case .accessibilityUntrusted: return "需要辅助功能权限"
        }
    }
}

/// The decision: do nothing, fill the box, or fill then send.
public enum SendPlan: Equatable {
    case refuse(RefuseReason)
    case fill
    case fillThenSend
}

/// All inputs to the decision — pure data, no OS coupling.
public struct SendContext {
    public let frontmostAllowed: Bool
    public let calibrated: Bool
    public let accessibilityTrusted: Bool
    public let autoSendOn: Bool
    public let stale: Bool

    public init(
        frontmostAllowed: Bool,
        calibrated: Bool,
        accessibilityTrusted: Bool,
        autoSendOn: Bool,
        stale: Bool
    ) {
        self.frontmostAllowed = frontmostAllowed
        self.calibrated = calibrated
        self.accessibilityTrusted = accessibilityTrusted
        self.autoSendOn = autoSendOn
        self.stale = stale
    }
}

/// The single home of every safety decision. Fail-closed: any failed
/// precondition refuses before any synthetic event is posted.
public enum SendPlanner {
    public static func plan(_ ctx: SendContext) -> SendPlan {
        if !ctx.accessibilityTrusted { return .refuse(.accessibilityUntrusted) }
        if !ctx.calibrated { return .refuse(.notCalibrated) }
        if !ctx.frontmostAllowed { return .refuse(.wrongApp) }
        if ctx.autoSendOn && !ctx.stale { return .fillThenSend }
        return .fill
    }
}
