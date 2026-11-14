import AppKit
import SwiftUI
import XCTest

private typealias F = StatusFixtures

/// Settings, the first run and the status drawn on fictional answers, in light and dark, into
/// shells/apple/build/screens/, to be looked at: each must draw something of its size in both.
@MainActor
final class StatusScreenTests: XCTestCase {
    private static let screens = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent().deletingLastPathComponent().appending(path: "build/screens")

    private let now = F.at("2026-11-14T15:00:00Z")

    private func status(failing: Bool = false, firstRun: Bool = false) -> AppStatus {
        let status = AppStatus(newMail: NewMailAlerts(notifier: SystemNotifier.Silent(), isActive: { true },
                                                      badge: { _ in }, announce: { _ in }))
        if firstRun {
            status.hold(digest: F.digest(accounts: [], aliases: [:], engine: "none", keyFrom: ""),
                        preferences: F.preferences(), now: now)
            return status
        }
        let accounts = [
            F.account("ada@example.test", alias: "Ada", type: "gmail", signature: "Ada\\nat the example"),
            F.account("news@example.test", type: "imap", style: "op", ref: "op://Private/News/password"),
            F.account("bridge@example.test", alias: "Proton", type: "proton", style: "proton_bridge"),
        ]
        let health = [
            F.standing("ada@example.test", ok: "2026-11-14T14:52:00Z", count: 12),
            failing
                ? F.standing("news@example.test", ok: "2026-11-14T09:00:00Z", error: "LOGIN refused", kind: "AUTH",
                             errorAt: "2026-11-14T14:30:00Z", count: 4)
                : F.standing("news@example.test", ok: "2026-11-14T14:50:00Z", count: 5),
            F.standing("bridge@example.test", ok: "2026-11-14T14:40:00Z", error: "pin", kind: "TLS_PIN",
                       errorAt: "2026-11-14T14:45:00Z", count: 2),
        ]
        status.hold(digest: F.digest(accounts: accounts.compactMap(\.email),
                                     aliases: ["ada@example.test": "Ada", "bridge@example.test": "Proton"],
                                     last: "2026-11-14T14:52:00Z"),
                    accounts: accounts, health: failing ? health : Array(health.prefix(2)),
                    preferences: F.preferences(quiet: true), upcoming: [F.meeting("Design review", start: now.addingTimeInterval(240))],
                    now: now)
        return status
    }

    /// Draw `view` at `size` in light and dark, write each PNG, and say where.
    @discardableResult
    private func render<V: View>(_ name: String, _ size: CGSize, _ view: @autoclosure () -> V) throws -> [URL] {
        try FileManager.default.createDirectory(at: Self.screens, withIntermediateDirectories: true)
        var written: [URL] = []
        for (mode, appearance) in [("light", NSAppearance.Name.aqua), ("dark", .darkAqua)] {
            let host = NSHostingView(rootView: view()
                .frame(width: size.width, height: size.height)
                .background(Color(nsColor: .windowBackgroundColor)))
            host.frame = CGRect(origin: .zero, size: size)
            let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
            window.appearance = NSAppearance(named: appearance)
            window.contentView = host
            host.layoutSubtreeIfNeeded()
            RunLoop.main.run(until: Date().addingTimeInterval(0.4))
            host.layoutSubtreeIfNeeded()
            let png = try XCTUnwrap(Self.drawn(host))
            let url = Self.screens.appending(path: "\(name)-\(mode).png")
            try png.write(to: url)
            XCTAssertGreaterThan(png.count, 4000, "\(name) in \(mode) drew something")
            written.append(url)
            window.contentView = nil
        }
        return written
    }

    /// The view's layers drawn into a bitmap at twice its size, so the controls AppKit draws in layers of their own (a
    /// default button's) are drawn too; else its own drawing, as AppKit caches it.
    private static func drawn(_ host: NSView) -> Data? {
        let scale: CGFloat = 2
        if let layer = host.layer,
           let context = CGContext(data: nil, width: Int(host.bounds.width * scale), height: Int(host.bounds.height * scale),
                                   bitsPerComponent: 8, bytesPerRow: 0, space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                   bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
        {
            context.scaleBy(x: scale, y: scale)
            // A layer draws top down; a bitmap's rows go bottom up.
            context.translateBy(x: 0, y: host.bounds.height)
            context.scaleBy(x: 1, y: -1)
            layer.render(in: context)
            if let image = context.makeImage() {
                return NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:])
            }
        }
        guard let rep = host.bitmapImageRepForCachingDisplay(in: host.bounds) else { return nil }
        host.cacheDisplay(in: host.bounds, to: rep)
        return rep.representation(using: .png, properties: [:])
    }

    func testSettingsAccounts() throws {
        let status = status(failing: true)
        try render("settings-accounts", CGSize(width: 760, height: 500),
                   AccountsPane(status: status, chosen: "ada@example.test").padding(20))
        try render("settings-account-pin", CGSize(width: 760, height: 560),
                   AccountsPane(status: status, chosen: "bridge@example.test").padding(20))
    }

    func testTheAccountForm() throws {
        var draft = AccountDraft()
        draft.email = "ada@example.test"
        draft.kind = .imap
        draft.host = "imap.example.test"
        try render("settings-account-form", CGSize(width: 520, height: 640),
                   AccountForm(status: status(), draft: draft, done: { _ in }))
    }

    func testSettingsPreferences() throws {
        let status = status()
        try render("settings-general", CGSize(width: 560, height: 540), GeneralPane(status: status))
        try render("settings-notifications", CGSize(width: 560, height: 400),
                   NotificationsPane(status: status, allowed: .some(false)))
        try render("settings-ai", CGSize(width: 560, height: 560), AIPane(status: status, keySet: true))
    }

    func testTheFirstRun() throws {
        try render("first-run", CGSize(width: 900, height: 640), Welcome(status: status(firstRun: true), keySet: false))
    }

    func testTheStatus() throws {
        let failing = status(failing: true)
        try render("status-menu", CGSize(width: 340, height: 420), StatusMenu(status: failing, close: {}))
        try render("status-bar", CGSize(width: 900, height: 120), VStack(spacing: 0) {
            HStack(spacing: 10) {
                StatusPill(status: failing)
                SoonPill(status: failing)
            }
            .padding(8)
            AccountAlert(status: failing)
            Spacer()
        })
        try render("status-menu-ok", CGSize(width: 340, height: 300), StatusMenu(status: status(), close: {}))
    }

    func testHelpAndTheGuide() throws {
        try render("help", CGSize(width: 520, height: 560), HelpWindow(status: status()))
        try render("guide", CGSize(width: 420, height: 230),
                   GuideSheet(steps: Guide.steps(ai: true, singleKeys: true), end: {}))
    }
}
