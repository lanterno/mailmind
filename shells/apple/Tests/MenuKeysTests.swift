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

    /// File, Edit, Message, Mailbox, Assistant and Window as the menu bar holds them for the window in front, as
    /// AppKit's menus, with the mail window open or closed as `door` says, and the window AppKit says is key as
    /// `keyWindow` heard it, where a test says.
    private func bar(mailbox: Mailbox?, window: MessageWindowVerbs?, writing: MailWriting?,
                     door: MailWindowDoor = .alwaysOpen, keyWindow: KeyWindow? = nil) -> NSMenu {
        let tools = ReaderTools(print: {}, find: { [weak self] in self?.found += 1 })
        let status = AppStatus(newMail: NewMailAlerts(notifier: SystemNotifier.Silent(), isActive: { true },
                                                      badge: { _ in }, announce: { _ in }))
        let menus: [(String, AnyView)] = (keyWindow.map { [("File", AnyView(CloseItem(keyWindow: $0)))] } ?? []) + [
            ("Edit", AnyView(FindInMessageItem(tools: tools, messageWindow: window != nil, window: window, door: door))),
            ("Message", AnyView(MessageMenuItems(mailbox: mailbox, window: window, writing: writing,
                                                 keyWindow: keyWindow, door: door))),
            ("Mailbox", AnyView(MailboxMenuItems(mailbox: mailbox, door: door))),
            ("Assistant", AnyView(AssistantMenuItems(status: status, door: door))),
            ("Window", AnyView(MailWindowItem(door: door))),
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

    /// The mail window as a test opens and closes it, and how often the menus opened it again.
    private final class Door {
        var open = false
        var reopened = 0
    }

    /// The titles of `menu`'s items in a menu bar, as AppKit shows them.
    private func titles(_ menu: String, in bar: NSMenu) -> [String] {
        bar.item(withTitle: menu)?.submenu?.items.map(\.title) ?? []
    }

    /// Whether the item titled `title` in a menu bar is enabled.
    private func enabled(_ title: String, in bar: NSMenu) throws -> Bool {
        let items = bar.items.flatMap { $0.submenu?.items ?? [] }
        return try XCTUnwrap(items.first { $0.title == title }, "\(title) in \(items.map(\.title))").isEnabled
    }

    /// With the mail window closed, SwiftUI still hands the menus its mailbox, its desk and its message as they were,
    /// so their items once acted on a window that wasn't there. A place opens the window again and goes there
    /// (All Inboxes, a folder, Today's Brief); what acts on a message, search and the palette wait for it; Window
    /// offers it again; and a message's own window, still open, keeps its verbs.
    func testWithTheMailWindowClosedAPlaceOpensItAgainAndAMessagesVerbsWait() async throws {
        let app = MailApp()
        self.app = app
        let mailbox = app.mailbox
        mailbox.wire = Fixture.server(rows: Array(Fixture.inbox.prefix(6))).wire
        await mailbox.refresh()
        mailbox.tab = .all
        app.place = .folder(account: Fixture.ada, folder: "INBOX")
        let list = try XCTUnwrap(app.list)
        await until { list.copy(at: 0) != nil }
        let copy = MailCopy(try XCTUnwrap(list.copy(at: 0)))
        mailbox.open(copy)
        let writing = MailWriting(replyTo: copy, draft: nil, current: nil)
        let door = Door()
        let doors = MailWindowDoor(isOpen: { door.open }, mailbox: { mailbox }, reopen: { door.reopened += 1 })
        // What SwiftUI hands the menus once the mail window has closed: its mailbox and message as they were.
        let closed = { self.bar(mailbox: mailbox, window: nil, writing: writing, door: doors) }
        let zero: CGKeyCode = 29, one: CGKeyCode = 18, b: CGKeyCode = 11, k: CGKeyCode = 40, f: CGKeyCode = 3
        let r: CGKeyCode = 15, u: CGKeyCode = 32, a: CGKeyCode = 0
        let commandZero = try Self.press(zero, .maskCommand)
        try XCTSkipUnless(commandZero.charactersIgnoringModifiers == "0", "this Mac's keyboard has no 0 where a US one has")

        XCTAssertEqual(titles("Window", in: closed()), ["MailMind"], "Window offers the mail window again")
        XCTAssertEqual(fired(commandZero, in: closed), ["All Inboxes"])
        XCTAssertEqual(app.place, .allInboxes)
        XCTAssertEqual(door.reopened, 1, "the mail window opens again, on All Inboxes")
        XCTAssertEqual(fired(try Self.press(one, .maskCommand), in: closed), ["Inbox"])
        XCTAssertEqual(app.place?.isInbox, true)
        XCTAssertEqual(door.reopened, 2)
        XCTAssertEqual(fired(try Self.press(b, [.maskAlternate, .maskCommand]), in: closed), ["Today’s Brief"])
        XCTAssertEqual(app.place, .desk(.brief))
        XCTAssertEqual(door.reopened, 3)

        // What acts on a message waits for the window.
        let waits: [(CGKeyCode, CGEventFlags)] = [(r, .maskCommand), (a, [.maskControl, .maskCommand]),
                                                  (u, [.maskShift, .maskCommand]), (f, [.maskAlternate, .maskCommand])]
        for (code, held) in waits {
            XCTAssertEqual(fired(try Self.press(code, held), in: closed), [], "\(code) with \(held) waits")
        }
        for title in ["Reply", "Forward", "Archive", "Delete", "Next Unread", "Find in Message…"] {
            XCTAssertFalse(try enabled(title, in: closed()), "\(title) waits for the mail window")
        }
        XCTAssertEqual(door.reopened, 3, "none of them opened the window")

        // Search and the palette open it again, as a place does, and go there.
        XCTAssertEqual(fired(try Self.press(f, .maskCommand), in: closed), ["Search"])
        XCTAssertTrue(mailbox.searchFocus, "the search box takes the keys as the window shows")
        XCTAssertEqual(door.reopened, 4)
        XCTAssertEqual(fired(try Self.press(k, .maskCommand), in: closed), ["Command Palette…"])
        XCTAssertTrue(mailbox.paletteOpen)
        XCTAssertEqual(door.reopened, 5)
        mailbox.searchFocus = false
        mailbox.paletteOpen = false

        // A message's own window, still open, acts on its message as before; once closed, it acts on nothing.
        let verbs = MessageWindowVerbs(copy: copy, mailbox: mailbox, reader: ReaderModel(), routes: nil)
        let own = { self.bar(mailbox: nil, window: verbs, writing: writing, door: doors) }
        XCTAssertTrue(try enabled("Reply", in: own()))
        XCTAssertTrue(try enabled("Archive", in: own()))
        verbs.closed = true
        XCTAssertFalse(try enabled("Reply", in: own()), "a closed message window's message waits too")
        XCTAssertFalse(try enabled("Archive", in: own()))

        // Open again, the window's own values are acted on, and Window lists it as it lists every window.
        door.open = true
        let open = { self.bar(mailbox: mailbox, window: nil, writing: writing, door: doors) }
        XCTAssertEqual(titles("Window", in: open()), [])
        app.place = .folder(account: Fixture.ada, folder: "INBOX")
        await until { app.list?.copy(at: 0) != nil }
        mailbox.open(copy)
        XCTAssertTrue(try enabled("Archive", in: open()))
        XCTAssertTrue(try enabled("Reply", in: open()))
        XCTAssertEqual(fired(commandZero, in: open), ["All Inboxes"])
        XCTAssertEqual(app.place, .allInboxes)
        XCTAssertEqual(door.reopened, 5, "an open window isn't opened again")
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

    // MARK: The window AppKit says is key

    /// The keys a request's body names, wherever in it.
    private static func keys(_ value: JSONValue) -> [Int] {
        switch value {
        case .object(let fields):
            var found: [Int] = []
            if case .number(let key)? = fields["key"] { found.append(Int(key)) }
            return found + fields.filter { $0.key != "key" }.values.flatMap(keys)
        case .array(let items):
            return items.flatMap(keys)
        default:
            return []
        }
    }

    /// The keys each request to `path` named.
    private static func keys(_ server: FakeServer, _ path: String) -> [Int] {
        server.asked(path).flatMap { keys((try? JSONDecoder().decode(JSONValue.self, from: $0.body)) ?? .null) }
    }

    private static func content(_ shows: KeyWindow.Shows?, _ tracker: KeyWindow) -> AnyView {
        AnyView(Color.clear.frame(width: 240, height: 120).keyWindowShows(shows, in: tracker))
    }

    /// A window as `MessageWindow` and `ComposeWindow` make theirs, which says what it shows to `tracker`.
    private func tagged(_ shows: KeyWindow.Shows?, _ tracker: KeyWindow) -> (NSWindow, NSHostingView<AnyView>) {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 240, height: 120),
                              styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        let host = NSHostingView(rootView: Self.content(shows, tracker))
        host.frame = window.contentLayoutRect
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        return (window, host)
    }

    /// A message's own window, key: SwiftUI handed the menus none of its values there, or the mail window's, stale, so
    /// Message › Reply did nothing while it was in front. As AppKit says it is key, every item of the Message menu acts
    /// on its message, through the menu and through its key; and once the mail window is key again, on the mail
    /// window's, whatever SwiftUI still hands on.
    func testTheMessageMenuActsOnTheKeyMessageWindowsMessage() async throws {
        let server = OpenedMessageVerbsTests.server()
        server.on("POST", API.Post.Mute.path, API.OnThread(ok: true, threadId: "t90"))
        let app = MailApp()
        self.app = app
        let mailbox = app.mailbox
        mailbox.wire = server.wire
        await mailbox.refresh()
        let copy = OpenedMessageVerbsTests.away
        let reader = ReaderModel()
        reader.hold(copy, OpenedMessageVerbsTests.head(copy))
        // What SwiftUI hands the menus while the message's window is key: no window, and the mail window's message.
        let other = MailCopy(key: 1, folder: "INBOX", uid: 1, account: Fixture.ada)
        let stale = MailWriting(replyTo: other)

        let center = NotificationCenter()
        let tracker = KeyWindow(center: center)
        var verbs = MessageWindowVerbs(copy: copy, mailbox: mailbox, reader: reader, routes: server.wire)
        let (window, host) = tagged(.message(verbs), tracker)
        defer { window.close() }
        center.post(name: NSWindow.didBecomeKeyNotification, object: window)
        await until { tracker.message === verbs }
        XCTAssertEqual(tracker.writing(focused: stale), MailWriting(replyTo: copy), "Reply answers the window's message")
        let menus = { self.bar(mailbox: mailbox, window: nil, writing: stale, keyWindow: tracker) }
        // A fresh window over the same message, once a verb has sent the last one's away.
        func again() async {
            verbs = MessageWindowVerbs(copy: copy, mailbox: mailbox, reader: reader, routes: server.wire)
            host.rootView = Self.content(.message(verbs), tracker)
            host.layoutSubtreeIfNeeded()
            await until { tracker.message === verbs }
        }

        let a: CGKeyCode = 0, f: CGKeyCode = 3, r: CGKeyCode = 15, u: CGKeyCode = 32, l: CGKeyCode = 37
        let m: CGKeyCode = 46, delete: CGKeyCode = 51
        let commandR = try Self.press(r, .maskCommand)
        try XCTSkipUnless(commandR.charactersIgnoringModifiers == "r", "this Mac's keyboard has no R where a US one has")

        // Reply, Reply All and Forward, on the window's message.
        XCTAssertEqual(fired(commandR, in: menus), ["Reply"])
        XCTAssertEqual(fired(try Self.press(r, [.maskShift, .maskCommand]), in: menus), ["Reply All"])
        XCTAssertEqual(fired(try Self.press(f, [.maskShift, .maskCommand]), in: menus), ["Forward"])

        // Mark, Star and Reply Later ask the server about it, and leave it in the window.
        XCTAssertEqual(fired(try Self.press(u, [.maskShift, .maskCommand]), in: menus).count, 1)
        await until { !server.asked(API.Post.Seen.path).isEmpty }
        XCTAssertEqual(Self.keys(server, API.Post.Seen.path), [copy.key])
        XCTAssertEqual(fired(try Self.press(l, [.maskShift, .maskCommand]), in: menus).count, 1)
        await until { !server.asked(API.Post.Flag.path).isEmpty }
        XCTAssertEqual(Self.keys(server, API.Post.Flag.path), [copy.key])
        XCTAssertEqual(fired(try Self.press(l, [.maskControl, .maskCommand]), in: menus), ["Reply Later"])
        await until { !server.asked(API.Post.Replylater.path).isEmpty }
        XCTAssertEqual(Self.keys(server, API.Post.Replylater.path), [copy.key])

        // Move To… asks where, over the window.
        XCTAssertEqual(fired(try Self.press(m, [.maskShift, .maskCommand]), in: menus), ["Move To…"])
        XCTAssertEqual(verbs.moving?.from, copy.folder)
        verbs.moving = nil

        // Archive, Delete, Mute and Snooze each send it away, and the window closes.
        XCTAssertEqual(fired(try Self.press(a, [.maskControl, .maskCommand]), in: menus), ["Archive"])
        await until { verbs.left }
        XCTAssertEqual(Self.keys(server, API.Post.Archive.path), [copy.key])
        await again()
        XCTAssertEqual(fired(try Self.press(delete, .maskCommand), in: menus), ["Delete"])
        await until { verbs.left }
        XCTAssertEqual(Self.keys(server, API.Post.Trash.path), [copy.key])
        await again()
        XCTAssertEqual(fired(try Self.press(m, [.maskControl, .maskCommand]), in: menus), ["Mute Thread"])
        await until { verbs.left }
        XCTAssertEqual(Self.keys(server, API.Post.Mute.path), [copy.key])
        await again()
        let snooze = try XCTUnwrap(menus().item(withTitle: "Message")?.submenu?.item(withTitle: "Snooze")?.submenu)
        snooze.update()
        let first = try XCTUnwrap(snooze.items.firstIndex { $0.isEnabled && !$0.title.isEmpty },
                                  "\(snooze.items.map(\.title))")
        snooze.performActionForItem(at: first)
        await until { verbs.left }
        XCTAssertEqual(Self.keys(server, API.Post.Snooze.path), [copy.key])

        // The mail window key again: the menus act on its mailbox, though SwiftUI hands on the message window still.
        await again()
        let mail = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 100, height: 100), styleMask: [.titled, .closable],
                            backing: .buffered, defer: false)
        mail.isReleasedWhenClosed = false
        defer { mail.close() }
        center.post(name: NSWindow.didResignKeyNotification, object: window)
        center.post(name: NSWindow.didBecomeKeyNotification, object: mail)
        XCTAssertNil(tracker.messageWindow(focused: verbs))
        XCTAssertEqual(tracker.writing(focused: stale), stale)
    }

    /// File › Close closes the window in front, and waits with none open; a popover's panel closes the window it
    /// hangs from.
    func testCloseClosesTheKeyWindowAndWaitsWithNone() async throws {
        let center = NotificationCenter()
        let tracker = KeyWindow(center: center)
        let (window, _) = tagged(nil, tracker)
        defer { window.close() }
        let w: CGKeyCode = 13
        let commandW = try Self.press(w, .maskCommand)
        try XCTSkipUnless(commandW.charactersIgnoringModifiers == "w", "this Mac's keyboard has no W where a US one has")
        let menus = { self.bar(mailbox: nil, window: nil, writing: nil, keyWindow: tracker) }

        XCTAssertFalse(try enabled("Close", in: menus()), "no window has said it is key")
        XCTAssertEqual(fired(commandW, in: menus), [])

        let panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 80, height: 40), styleMask: [.borderless],
                            backing: .buffered, defer: false)
        panel.isReleasedWhenClosed = false
        window.addChildWindow(panel, ordered: .above)
        defer { panel.close() }
        center.post(name: NSWindow.didBecomeKeyNotification, object: panel)
        XCTAssertTrue(try enabled("Close", in: menus()), "a popover's panel stands for its window")
        XCTAssertTrue(tracker.closes === window)
        window.removeChildWindow(panel)

        center.post(name: NSWindow.didBecomeKeyNotification, object: window)
        window.orderFront(nil)
        XCTAssertTrue(window.isVisible)
        XCTAssertEqual(fired(commandW, in: menus), ["Close"])
        XCTAssertFalse(window.isVisible, "the window in front closed")
        center.post(name: NSWindow.willCloseNotification, object: window)
        XCTAssertFalse(try enabled("Close", in: menus()), "with none in front, Close waits")
        XCTAssertEqual(fired(commandW, in: menus), [])
    }

    /// With the mail window closed, a panel of the Assistant menu opens it again with the panel over it, as Today's
    /// Brief opens it on the brief, rather than waiting.
    func testWithTheMailWindowClosedAPanelOpensItAgainOverIt() async throws {
        let app = MailApp()
        self.app = app
        let mailbox = app.mailbox
        mailbox.wire = Fixture.server().wire
        await mailbox.refresh()
        let door = Door()
        let doors = MailWindowDoor(isOpen: { door.open }, mailbox: { mailbox }, reopen: { door.reopened += 1 })
        let closed = { self.bar(mailbox: mailbox, window: nil, writing: nil, door: doors) }
        defer { Desk.asked = [] }
        let panels: [(String, String)] = [("Ask Your Mail…", "ask"), ("Commitments and Tasks", "commitments"),
                                              ("Your Week", "review"), ("Curate — Rules and Senders…", "curate")]
        for (index, (title, sheet)) in panels.enumerated() {
            XCTAssertTrue(try enabled(title, in: closed()), "\(title) opens the window again")
            let menu = try XCTUnwrap(closed().item(withTitle: "Assistant")?.submenu)
            menu.performActionForItem(at: try XCTUnwrap(menu.items.firstIndex { $0.title == title }))
            XCTAssertEqual(door.reopened, index + 1)
            // The window opened again makes its desk, which opens the panel as it joins.
            let desk = Desk()
            desk.use(app, routes: DeskRoutes())
            XCTAssertEqual(desk.sheet?.id, sheet, title)
            XCTAssertTrue(Desk.asked.isEmpty, "once")
        }
        let ask = try Self.press(0, [.maskAlternate, .maskCommand])
        XCTAssertEqual(fired(ask, in: closed), ["Ask Your Mail…"], "and through its key")
        XCTAssertEqual(door.reopened, panels.count + 1)
        let desk = Desk()
        desk.use(app, routes: DeskRoutes())
        XCTAssertEqual(desk.sheet?.id, "ask")
    }

    /// Message › Send Later… opens the Later menu of the composer whose window is key, as its Send ▾ does; with none
    /// in front it waits. The page gives it no key, so it has none.
    func testSendLaterOpensTheKeyComposersLaterMenu() async throws {
        let center = NotificationCenter()
        let tracker = KeyWindow(center: center)
        let composer = Composer(ComposeSeed(), host: FakeHost())
        let (window, _) = tagged(.composer(composer), tracker)
        defer { window.close() }
        let menus = { self.bar(mailbox: nil, window: nil, writing: nil, keyWindow: tracker) }
        XCTAssertFalse(try enabled("Send Later…", in: menus()), "no composer in front")

        center.post(name: NSWindow.didBecomeKeyNotification, object: window)
        await until { tracker.composer === composer }
        XCTAssertTrue(try enabled("Send Later…", in: menus()))
        let bar = menus()
        let menu = try XCTUnwrap(bar.item(withTitle: "Message")?.submenu)
        let at = try XCTUnwrap(menu.items.firstIndex { $0.title == "Send Later…" })
        XCTAssertEqual(menu.items[at].keyEquivalent, "", "the page has no key for it")
        menu.performActionForItem(at: at)
        XCTAssertTrue(composer.laterOpen, "its Later menu opens")

        center.post(name: NSWindow.didResignKeyNotification, object: window)
        XCTAssertFalse(try enabled("Send Later…", in: menus()), "its window no longer in front")
    }
}
