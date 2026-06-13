import Testing
@testable import GlimpseShellLib

private func ctx(
    frontmostAllowed: Bool = true,
    calibrated: Bool = true,
    accessibilityTrusted: Bool = true,
    autoSendOn: Bool = false,
    stale: Bool = false
) -> SendContext {
    SendContext(
        frontmostAllowed: frontmostAllowed,
        calibrated: calibrated,
        accessibilityTrusted: accessibilityTrusted,
        autoSendOn: autoSendOn,
        stale: stale
    )
}

@Test
func refusesWhenAccessibilityUntrusted() {
    // Can't post any synthetic event without Accessibility trust.
    #expect(SendPlanner.plan(ctx(accessibilityTrusted: false)) == .refuse(.accessibilityUntrusted))
}

@Test
func refusesWhenNotCalibrated() {
    // No calibrated point ⇒ clicking would target a meaningless location.
    #expect(SendPlanner.plan(ctx(calibrated: false)) == .refuse(.notCalibrated))
}

@Test
func refusesWhenWrongAppFrontmost() {
    // The disaster guard: never paste/send unless the chat app owns the screen.
    #expect(SendPlanner.plan(ctx(frontmostAllowed: false)) == .refuse(.wrongApp))
}

@Test
func fillsWhenAutoSendOff() {
    #expect(SendPlanner.plan(ctx(autoSendOn: false)) == .fill)
}

@Test
func staleDowngradesAutoSendToFill() {
    // The stale-block: never auto-send a reply the conversation has outrun.
    #expect(SendPlanner.plan(ctx(autoSendOn: true, stale: true)) == .fill)
}

@Test
func sendsOnlyWhenAutoSendOnAndNotStaleAndChecksPass() {
    #expect(SendPlanner.plan(ctx(autoSendOn: true, stale: false)) == .fillThenSend)
}

@Test
func accessibilityRefusalTakesPrecedenceWhenMultipleGuardsFail() {
    // The guard order is deliberate: when several preconditions fail at once, the
    // most fundamental one (no Accessibility ⇒ no synthetic event is possible)
    // must win, so the user sees the actionable blocker first.
    #expect(
        SendPlanner.plan(ctx(frontmostAllowed: false, calibrated: false, accessibilityTrusted: false))
            == .refuse(.accessibilityUntrusted)
    )
}

@Test
func refuseReasonsCarryUserFacingMessages() {
    #expect(RefuseReason.wrongApp.message == "切换到微信再发送")
    #expect(RefuseReason.notCalibrated.message == "先设置输入框位置")
    #expect(RefuseReason.accessibilityUntrusted.message == "需要辅助功能权限")
}
