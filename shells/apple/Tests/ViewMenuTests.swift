import AppKit
import SwiftUI
import XCTest

/// The View menu's group as Meadow puts it in place of the sidebar's, drawn as AppKit's menu: Organize by
/// Conversation and the Inbox's tabs with their keys are there, beside the rail, focus reading and the light.
@MainActor
final class ViewMenuTests: XCTestCase {
    private var app: MailApp?
    /// A suite of its own, so nothing the shell saves reaches the app's own defaults.
    private let suite = "view-menu-\(UUID().uuidString)"

    override func tearDown() async throws {
        app = nil
        UserDefaults().removePersistentDomain(forName: suite)
    }

    /// The mailbox on its Inbox, the list showing Everything.
    private func mailbox() async -> Mailbox {
        let app = MailApp()
        self.app = app
        app.mailbox.wire = Fixture.server(rows: Array(Fixture.inbox.prefix(6))).wire
        await app.mailbox.refresh()
        app.mailbox.tab = .all
        app.openList()
        return app.mailbox
    }

    /// The group's items as the menu bar shows them, sections opened out.
    private func items(_ view: some View) -> [NSMenuItem] {
        let menu = NSHostingMenu(rootView: view)
        menu.update()
        return menu.items.flatMap { item in [item] + (item.submenu?.items ?? []) }
    }

    private func item(_ title: String, in items: [NSMenuItem]) throws -> NSMenuItem {
        try XCTUnwrap(items.first { $0.title == title }, "View offers \(title); it has \(items.map(\.title))")
    }

    func testViewOffersConversationsAndTheInboxsTabsWithTheirKeys() async throws {
        let mailbox = await mailbox()
        let shell = MeadowShell(defaults: try XCTUnwrap(UserDefaults(suiteName: suite)))
        let items = items(MeadowViewMenu(shell: shell, mailbox: mailbox))
        let grouping = try item("Organize by Conversation", in: items)
        XCTAssertEqual(grouping.state, mailbox.grouped ? .on : .off)
        for (index, tab) in InboxTab.allCases.enumerated() {
            let shown = try item(tab.title, in: items)
            XCTAssertEqual(shown.keyEquivalent, String(index + 1), "\(tab.title) is ⌥⌘\(index + 1)")
            XCTAssertEqual(shown.keyEquivalentModifierMask, [.option, .command])
            XCTAssertTrue(shown.isEnabled, "on the Inbox")
            XCTAssertEqual(shown.state, tab == .all ? .on : .off, "the tab on screen is ticked")
        }
        XCTAssertNotNil(items.first { $0.title.contains("Mailbox") }, "the rail's item is there too")
        XCTAssertNotNil(items.first { $0.title.contains("Focus Reading") }, "and focus reading")
    }

    func testTheInboxsTabKeysChangeTheTab() async throws {
        let mailbox = await mailbox()
        let items = items(MailboxViewItems(mailbox: mailbox))
        let unread = try item(InboxTab.unread.title, in: items)
        _ = (unread.target as AnyObject?)?.perform(try XCTUnwrap(unread.action), with: unread)
        await until { mailbox.tab == .unread }
    }

    func testTheTabsWaitForTheInbox() async throws {
        let mailbox = await mailbox()
        mailbox.app?.place = .folder(account: Fixture.ada, folder: "Archive")
        let items = items(MailboxViewItems(mailbox: mailbox))
        XCTAssertFalse(try item(InboxTab.needs.title, in: items).isEnabled, "a folder has no tabs")
        XCTAssertTrue(try item("Organize by Conversation", in: items).isEnabled)
    }

    /// The brief stands in the pane beside the Inbox's list, whose head shows the tabs: View's tabs are there too, and
    /// act as the tabs do, the brief staying where it is. An answer stands in the list's place, with no tabs.
    func testTheTabsStandBesideTheBrief() async throws {
        let mailbox = await mailbox()
        let app = try XCTUnwrap(mailbox.app)
        app.place = .desk(.brief)
        XCTAssertEqual(app.listed, .allInboxes, "the Inbox's list beside the brief")
        let items = items(MailboxViewItems(mailbox: mailbox))
        for tab in InboxTab.allCases {
            XCTAssertTrue(try item(tab.title, in: items).isEnabled, "\(tab.title) beside the brief")
        }
        let unread = try item(InboxTab.unread.title, in: items)
        _ = (unread.target as AnyObject?)?.perform(try XCTUnwrap(unread.action), with: unread)
        await until { mailbox.tab == .unread }
        XCTAssertEqual(app.place, .desk(.brief), "the brief stays")

        app.place = .desk(.ask)
        let asked = self.items(MailboxViewItems(mailbox: mailbox))
        XCTAssertFalse(try item(InboxTab.needs.title, in: asked).isEnabled, "an answer has no tabs")
    }
}
