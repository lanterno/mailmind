import AppKit
import SwiftUI
import XCTest

/// The menu bar's keys as a Mac's keyboard makes them, pressed through AppKit's menus drawn from the menu bar's own
/// items (Edit's Find in Message, then the Message and Mailbox menus, in the menu bar's order), with the mail window
/// in front and with a message's own window: which item each key fires.
@MainActor
final class MenuKeysTests: XCTestCase {
    private var app: MailApp?
    private var found = 0

    override func setUp() async throws {
        // AppKit sends a menu item's action through the app, as the menu bar does.
        _ = NSApplication.shared
    }

    override func tearDown() async throws {
        app = nil
    }

    /// A key as the window server hands it to AppKit: the key's code with what is held, its characters worked out by
    /// the Mac's keyboard layout. An `NSEvent` made with `keyEvent(with:…)` has no layout behind it, and AppKit's
    /// menus match it as no keyboard would: ⌘F fires ⇧⌘F's Forward and ⇧⌘F nothing, as a test tool's keys once did.
    private static func press(_ code: CGKeyCode, _ held: CGEventFlags) throws -> NSEvent {
        let made = try XCTUnwrap(CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: true))
        made.flags = held
        return try XCTUnwrap(NSEvent(cgEvent: made))
    }

    /// Over every account, Mailbox › Drafts (⌘3) goes to the first account's Drafts, as the page's g d does, and
    /// says whose it is and why; from one account's Inbox, Sent (⌘2) goes to that account's, with nothing to say.
    func testAFoldersKeyOverEveryAccountSaysWhoseFolderItOpened() async throws {
        let app = MailApp()
        self.app = app
        let mailbox = app.mailbox
        mailbox.wire = Fixture.server(rows: Array(Fixture.inbox.prefix(6))).wire
        await mailbox.refresh()
        app.openList()
        XCTAssertEqual(app.place, .allInboxes)
        let drafts = try Self.press(20, .maskCommand)
        XCTAssertEqual(fired(drafts) { self.bar(mailbox: mailbox, window: nil, writing: nil) }, ["Drafts"], "enabled, and fired")
        XCTAssertEqual(app.place, .folder(account: Fixture.ada, folder: "Drafts"))
        let said = try XCTUnwrap(mailbox.toast?.text)
        XCTAssertTrue(said.hasPrefix("\(mailbox.name(of: Fixture.ada))’s Drafts"), said)
        XCTAssertTrue(said.contains("All accounts brings only the inboxes together"), said)

        let before = mailbox.toast?.id
        app.place = .folder(account: Fixture.work, folder: "INBOX")
        let sent = try Self.press(19, .maskCommand)
        XCTAssertEqual(fired(sent) { self.bar(mailbox: mailbox, window: nil, writing: nil) }, ["Sent"])
        XCTAssertEqual(app.place, .folder(account: Fixture.work, folder: "[Gmail]/Sent Mail"), "the account in front's own")
        XCTAssertEqual(mailbox.toast?.id, before, "with nothing more to say")
    }

    /// Edit, Message and Mailbox as the menu bar holds them for the window in front, as AppKit's menus.
    private func bar(mailbox: Mailbox?, window: MessageWindowVerbs?, writing: MailWriting?) -> NSMenu {
        let tools = ReaderTools(print: {}, find: { [weak self] in self?.found += 1 })
        let menus: [(String, AnyView)] = [
            ("Edit", AnyView(FindInMessageItem(tools: tools, messageWindow: window != nil))),
            ("Message", AnyView(MessageMenuItems(mailbox: mailbox, window: window, writing: writing))),
            ("Mailbox", AnyView(MailboxMenuItems(mailbox: mailbox))),
        ]
        let bar = NSMenu(title: "Main")
        for (title, items) in menus {
            let menu = NSHostingMenu(rootView: items)
            menu.title = title
            menu.update()
            let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
            item.submenu = menu
            bar.addItem(item)
        }
        return bar
    }

    private final class Fired: @unchecked Sendable {
        var titles: [String] = []
    }

    /// The items `event` fires in a menu bar `make` draws afresh, by their titles.
    private func fired(_ event: NSEvent, in make: () -> NSMenu) -> [String] {
        let bar = make()
        let fired = Fired()
        let token = NotificationCenter.default.addObserver(forName: NSMenu.didSendActionNotification, object: nil,
                                                           queue: nil) { note in
            if let item = note.userInfo?["MenuItem"] as? NSMenuItem { fired.titles.append(item.title) }
        }
        defer { NotificationCenter.default.removeObserver(token) }
        _ = bar.performKeyEquivalent(with: event)
        return fired.titles
    }

    func testEachKeyFiresItsOwnItemInTheMailWindowAndAMessagesWindow() async throws {
        let app = MailApp()
        self.app = app
        let mailbox = app.mailbox
        mailbox.wire = Fixture.server(rows: Array(Fixture.inbox.prefix(6))).wire
        await mailbox.refresh()
        mailbox.tab = .all
        app.openList()
        let list = try XCTUnwrap(app.list)
        await until { list.copy(at: 0) != nil }
        let copy = MailCopy(try XCTUnwrap(list.copy(at: 0)))
        mailbox.open(copy)
        let writing = MailWriting(replyTo: copy, draft: nil, current: nil)
        let verbs = MessageWindowVerbs(copy: copy, mailbox: mailbox, reader: ReaderModel(), routes: nil)
        let main = { self.bar(mailbox: mailbox, window: nil, writing: writing) }
        let own = { self.bar(mailbox: nil, window: verbs, writing: writing) }

        // F and U, where a US keyboard has them.
        let f: CGKeyCode = 3, u: CGKeyCode = 32
        let commandF = try Self.press(f, .maskCommand)
        try XCTSkipUnless(commandF.charactersIgnoringModifiers == "f", "this Mac's keyboard has no F where a US one has")
        XCTAssertEqual(try Self.press(f, [.maskShift, .maskCommand]).charactersIgnoringModifiers, "F")
        XCTAssertEqual(try Self.press(u, .maskCommand).charactersIgnoringModifiers, "u")
        XCTAssertEqual(try Self.press(u, [.maskShift, .maskCommand]).charactersIgnoringModifiers, "U")

        // The mail window: ⌘F searches, ⇧⌘F forwards, ⇧⌘U marks, ⌘U is nothing's, ⌥⌘F finds in the message.
        XCTAssertEqual(fired(commandF, in: main), ["Search"])
        await until { mailbox.searchFocus }
        XCTAssertEqual(fired(try Self.press(f, [.maskShift, .maskCommand]), in: main), ["Forward"])
        XCTAssertEqual(fired(try Self.press(u, .maskCommand), in: main), [])
        let marked = fired(try Self.press(u, [.maskShift, .maskCommand]), in: main)
        XCTAssertEqual(marked.count, 1)
        XCTAssertTrue(marked.first?.hasPrefix("Mark as") == true, "⇧⌘U marks: \(marked)")
        XCTAssertEqual(fired(try Self.press(f, [.maskAlternate, .maskCommand]), in: main), ["Find in Message…"])
        await until { self.found == 1 }

        // A message's own window: ⌘F finds in its message, as there is no search there; the rest as in the mail window.
        mailbox.searchFocus = false
        XCTAssertEqual(fired(commandF, in: own), ["Find in Message…"])
        await until { self.found == 2 }
        XCTAssertFalse(mailbox.searchFocus, "the mail window's search is left alone")
        XCTAssertEqual(fired(try Self.press(f, [.maskShift, .maskCommand]), in: own), ["Forward"])
        XCTAssertEqual(fired(try Self.press(u, .maskCommand), in: own), [])
        XCTAssertEqual(fired(try Self.press(u, [.maskShift, .maskCommand]), in: own), ["Mark as Unread"])
    }

    /// What the menus show beside each item: Forward and Mark as Unread hold ⇧, Search doesn't, and Find in Message is
    /// ⌥⌘F beside the mail window's search and ⌘F in a message's own window.
    func testTheMenusShowEachKey() throws {
        let writing = MailWriting(replyTo: MailCopy(key: 1, folder: "INBOX", uid: 1, account: Fixture.ada))
        func item(_ title: String, in view: some View) throws -> NSMenuItem {
            let menu = NSHostingMenu(rootView: view)
            menu.update()
            return try XCTUnwrap(menu.items.first { $0.title.hasPrefix(title) }, "\(title) in \(menu.items.map(\.title))")
        }
        let forward = try item("Forward", in: MessageMenuItems(mailbox: nil, window: nil, writing: writing))
        XCTAssertEqual(forward.keyEquivalent, "f")
        XCTAssertEqual(forward.keyEquivalentModifierMask, [.shift, .command])
        let mark = try item("Mark as Unread", in: MessageMenuItems(mailbox: nil, window: nil, writing: writing))
        XCTAssertEqual(mark.keyEquivalent, "u")
        XCTAssertEqual(mark.keyEquivalentModifierMask, [.shift, .command])
        let search = try item("Search", in: MailboxMenuItems(mailbox: nil))
        XCTAssertEqual(search.keyEquivalent, "f")
        XCTAssertEqual(search.keyEquivalentModifierMask, .command)
        let tools = ReaderTools(print: {}, find: {})
        let find = try item("Find in Message", in: FindInMessageItem(tools: tools))
        XCTAssertEqual(find.keyEquivalent, "f")
        XCTAssertEqual(find.keyEquivalentModifierMask, [.option, .command])
        let ownFind = try item("Find in Message", in: FindInMessageItem(tools: tools, messageWindow: true))
        XCTAssertEqual(ownFind.keyEquivalent, "f")
        XCTAssertEqual(ownFind.keyEquivalentModifierMask, .command)
    }
}
