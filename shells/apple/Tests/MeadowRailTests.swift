import AppKit
import XCTest

/// The rail's places in the mockup's order (`renderRail()`), open and folded, an account's folders and labels, and
/// the accounts' menu, on fictional mail.
@MainActor
final class MeadowRailTests: XCTestCase {
    private func mailbox() async -> Mailbox {
        let mailbox = Mailbox()
        mailbox.wire = Fixture.server().wire
        await mailbox.refresh()
        return mailbox
    }

    func testTheRailListsThePlacesInTheMockupsOrder() async {
        let mailbox = await mailbox()
        let needing = (1...3).map { DeskRail.Need(account: Fixture.ada, conversation: "t\($0)") }
        let entries = RailPlaces.entries(mailbox, desk: DeskRail(brief: true, needing: needing, gates: [
            DeskRail.Gate(account: Fixture.ada, waiting: 2),
        ]))
        let open = entries.filter { $0.shows(open: true) }.map(\.id)
        XCTAssertEqual(Array(open.prefix(12)), [
            "brief", "inbox", "gate:\(Fixture.ada)", "virtual:\(Place.virtual(.waiting, account: nil))",
            "virtual:\(Place.virtual(.replyLater, account: nil))", "virtual:\(Place.virtual(.snoozed, account: nil))",
            "head:lanes", "view:__lane_feed__", "view:__lane_papertrail__", "documents", "head:views", "view:v1",
        ])
        XCTAssertTrue(open.contains("head:folders"))
        XCTAssertTrue(open.contains("account:\(Fixture.ada)"))
        XCTAssertTrue(open.contains("folder:\(Fixture.ada)\u{0}Trash"))
        XCTAssertTrue(open.contains("labels:\(Fixture.work)"), "the Gmail account's labels fold")
        XCTAssertFalse(open.contains("sep"))
        XCTAssertFalse(open.contains("folders"))

        let inbox = entries.first { $0.id == "inbox" }?.item
        XCTAssertEqual(inbox?.count, .sun(3), "the Inbox counts what needs you in sunlight")
        XCTAssertEqual(entries.first { $0.id == "gate:\(Fixture.ada)" }?.item?.count, .badge(2))
        XCTAssertEqual(entries.first { $0.id == "view:v1" }?.item?.removable?.id, "v1")
        XCTAssertNil(entries.first { $0.id == "view:__lane_feed__" }?.item?.removable)
    }

    func testFoldedTheRailKeepsThePlacesOverEveryAccountAndOffersTheFolders() async {
        let mailbox = await mailbox()
        let entries = RailPlaces.entries(mailbox, desk: DeskRail(brief: true))
        let folded = entries.filter { $0.shows(open: false) }.map(\.id)
        XCTAssertTrue(folded.contains("sep"))
        XCTAssertTrue(folded.contains("folders"))
        XCTAssertFalse(folded.contains { $0.hasPrefix("head:") || $0.hasPrefix("account:") || $0.hasPrefix("folder:") })
        // Without anything that needs you, the Inbox counts every account's unread, which folds away with the names.
        XCTAssertEqual(entries.first { $0.id == "inbox" }?.item?.count, .plain(5))
        // A folder folded away is stood for by the Folders button; the Inbox by its own.
        let trash = Place.folder(account: Fixture.ada, folder: "Trash")
        XCTAssertEqual(RailPlaces.current(trash, in: entries, open: false), "folders")
        XCTAssertEqual(RailPlaces.current(trash, in: entries, open: true), "folder:\(Fixture.ada)\u{0}Trash")
        XCTAssertEqual(RailPlaces.current(.allInboxes, in: entries, open: false), "inbox")
        XCTAssertEqual(RailPlaces.current(.desk(.brief), in: entries, open: true), "brief")
        XCTAssertFalse(RailPlaces.places(entries, open: false).contains(trash), "the arrows skip what's folded")
    }

    /// The Inbox follows the account the window shows, as the page's rail does: that account's own, counting what
    /// needs you there, else its unread; every account's together under All accounts. What needs you counts as its
    /// list does, a conversation once where the list shows conversations.
    func testTheInboxCountsAndOpensTheAccountShownOrEveryAccount() async {
        let mailbox = await mailbox()
        let desk = DeskRail(brief: true, needing: [
            DeskRail.Need(account: Fixture.ada, conversation: "t1"), DeskRail.Need(account: Fixture.ada, conversation: "t1"),
            DeskRail.Need(account: Fixture.ada, conversation: "t2"), DeskRail.Need(account: Fixture.work, conversation: "t1"),
        ])
        XCTAssertEqual(desk.needs(in: nil, grouped: true), 3, "Ada's t1 once, and the work account's t1 apart")
        XCTAssertEqual(desk.needs(in: nil, grouped: false), 4)
        XCTAssertEqual(desk.needs(in: Fixture.ada, grouped: true), 2)
        XCTAssertEqual(desk.needs(in: Fixture.work, grouped: false), 1)

        func inbox(_ desk: DeskRail, scope: String?) -> RailItem? {
            RailPlaces.entries(mailbox, desk: desk, scope: scope).first { $0.id == "inbox" }?.item
        }
        XCTAssertTrue(mailbox.grouped)
        XCTAssertEqual(inbox(desk, scope: nil)?.place, .allInboxes)
        XCTAssertEqual(inbox(desk, scope: nil)?.count, .sun(3))
        XCTAssertEqual(inbox(desk, scope: Fixture.ada)?.place, .folder(account: Fixture.ada, folder: "INBOX"))
        XCTAssertEqual(inbox(desk, scope: Fixture.ada)?.count, .sun(2))
        XCTAssertEqual(inbox(desk, scope: Fixture.work)?.count, .sun(1))
        // Nothing needs you there: the Inbox's unread, as the server counts its messages.
        let none = DeskRail(brief: true, needing: [DeskRail.Need(account: Fixture.ada, conversation: "t1")])
        XCTAssertEqual(inbox(none, scope: Fixture.work)?.count, .plain(1))
        XCTAssertEqual(inbox(DeskRail(brief: true), scope: Fixture.ada)?.count, .plain(4))
        XCTAssertEqual(inbox(DeskRail(brief: true), scope: nil)?.count, .plain(5))
        // The account's Inbox is the rail's Inbox while the window shows that account.
        let scoped = RailPlaces.entries(mailbox, desk: desk, scope: Fixture.ada)
        XCTAssertEqual(RailPlaces.current(.folder(account: Fixture.ada, folder: "INBOX"), in: scoped, open: true), "inbox")
    }

    func testANestedFolderIsCalledByItsOwnNameAndGoneToByItsWholeOne() async {
        let server = Fixture.server()
        // As the server's live list names them, and as the rail kept for when it doesn't answer, whole.
        let nested = [Fixture.folder("Folders/Clients", role: "custom"), Fixture.folder("[Gmail]/Starred", role: "custom"),
                      Fixture.folder("Labels/Important", role: "custom")]
        var kept = Fixture.folder("Folders/Kept", role: "custom")
        kept.ownName = kept.name
        server.on("GET", API.Get.Folders.path, API.Folders(account: Fixture.ada, folders: Fixture.adaFolders + nested + [kept],
                                                            muted: 0, ok: true, replylater: 0, snoozed: 0, waiting: 0))
        let mailbox = Mailbox()
        mailbox.wire = server.wire
        await mailbox.refresh()
        let items = RailPlaces.entries(mailbox, desk: DeskRail(brief: true)).compactMap(\.item)
        for (name, title) in [("Folders/Clients", "Clients"), ("[Gmail]/Starred", "Starred"),
                              ("Labels/Important", "Important"), ("Folders/Kept", "Kept")] {
            let item = items.first { $0.place == .folder(account: Fixture.ada, folder: name) }
            XCTAssertEqual(item?.title, title, "the rail calls \(name) by its own name")
        }
        XCTAssertFalse(items.contains { $0.title.contains("/") }, "no path shows on the rail")
        XCTAssertEqual(Place.folder(account: Fixture.ada, folder: "Folders/Clients").title(folders: mailbox.folders(of: Fixture.ada)),
                       "Clients", "nor over the list")
    }

    func testFoldedEveryAccountsGateIsOneThatCountsEveryoneWaiting() async {
        let mailbox = await mailbox()
        func gates(_ ada: Int, _ work: Int) -> [RailEntry] {
            RailPlaces.entries(mailbox, desk: DeskRail(brief: true, gates: [
                DeskRail.Gate(account: Fixture.ada, waiting: ada), DeskRail.Gate(account: Fixture.work, waiting: work),
            ]))
        }
        func folded(_ entries: [RailEntry]) -> [RailItem] {
            entries.filter { $0.shows(open: false) }.compactMap(\.item).filter { $0.icon == .gate }
        }
        func open(_ entries: [RailEntry]) -> [RailItem] {
            entries.filter { $0.shows(open: true) }.compactMap(\.item).filter { $0.icon == .gate }
        }

        // Senders wait in both: open, a gate each, named; folded, one, counting them all, opening the first.
        let both = gates(2, 3)
        XCTAssertEqual(open(both).map(\.title), ["At the gate · Ada", "At the gate · work"])
        XCTAssertEqual(open(both).map(\.count), [.badge(2), .badge(3)])
        XCTAssertEqual(folded(both), [RailItem(place: .desk(.screener(account: Fixture.ada)), icon: .gate,
                                               title: "At the gate", count: .badge(5))])
        // Only in the second: it opens that one.
        let second = gates(0, 3)
        XCTAssertEqual(open(second).map(\.title), ["At the gate · work"], "named, as there are two accounts")
        XCTAssertEqual(folded(second).map(\.place), [.desk(.screener(account: Fixture.work))])
        XCTAssertEqual(folded(second).map(\.count), [.badge(3)])
        // No one: the open rail shows none, the folded rail its gate with no badge, opening the first account's.
        let none = gates(0, 0)
        XCTAssertEqual(open(none), [])
        XCTAssertEqual(folded(none).map(\.place), [.desk(.screener(account: Fixture.ada))])
        XCTAssertEqual(folded(none).map(\.count), [RailItem.Count.none])
        // One account names none.
        let one = RailPlaces.entries(mailbox, desk: DeskRail(brief: true, gates: [
            DeskRail.Gate(account: Fixture.ada, waiting: 2),
        ]))
        XCTAssertEqual(open(one).map(\.title), ["At the gate"])
        // Folded, the gate stands for any account's Screener.
        XCTAssertEqual(RailPlaces.current(.desk(.screener(account: Fixture.work)), in: both, open: false), "gate")
        XCTAssertEqual(RailPlaces.current(.desk(.screener(account: Fixture.work)), in: both, open: true),
                       "gate:\(Fixture.work)")
    }

    func testFoldedTheViewsWaitBehindOneButton() async {
        let mailbox = await mailbox()
        let entries = RailPlaces.entries(mailbox, desk: DeskRail(brief: true))
        let folded = entries.filter { $0.shows(open: false) }.map(\.id)
        XCTAssertTrue(folded.contains("views"))
        XCTAssertFalse(folded.contains("view:v1"), "a person's views fold away")
        XCTAssertTrue(folded.contains("view:__lane_feed__"), "the lanes stay, as the mockup's do")
        XCTAssertFalse(entries.filter { $0.shows(open: true) }.contains { $0.id == "views" })
        let view = try? XCTUnwrap(entries.first { $0.id == "view:v1" }?.item?.place)
        XCTAssertEqual(RailPlaces.current(view, in: entries, open: false), "views")
        XCTAssertEqual(RailPlaces.current(view, in: entries, open: true), "view:v1")

        // With no views of a person's own, no button.
        mailbox.views = mailbox.views.filter { Lane(view: $0.id) != nil }
        let lanes = RailPlaces.entries(mailbox, desk: DeskRail(brief: true))
        XCTAssertFalse(lanes.contains { $0.id == "views" })
    }

    func testTheArrowsGoThroughThePlacesTheRailShows() async throws {
        let mailbox = await mailbox()
        let entries = RailPlaces.entries(mailbox, desk: DeskRail(brief: true, gates: [
            DeskRail.Gate(account: Fixture.ada, waiting: 2), DeskRail.Gate(account: Fixture.work, waiting: 3),
        ]))
        let ada = Place.desk(.screener(account: Fixture.ada)), work = Place.desk(.screener(account: Fixture.work))
        let waiting = Place.virtual(.waiting, account: nil)
        func step(_ place: Place?, _ by: Int, open: Bool) -> String? {
            RailPlaces.step(from: place, by: by, in: entries, open: open)?.id
        }
        // Open: each gate in turn.
        XCTAssertEqual(step(.allInboxes, 1, open: true), "gate:\(Fixture.ada)")
        XCTAssertEqual(step(ada, 1, open: true), "gate:\(Fixture.work)")
        XCTAssertEqual(step(work, 1, open: true), "virtual:\(waiting)")
        // Folded: the one gate, from either account's Screener on.
        XCTAssertEqual(step(.allInboxes, 1, open: false), "gate")
        XCTAssertEqual(RailPlaces.step(from: .allInboxes, by: 1, in: entries, open: false)?.place, ada)
        XCTAssertEqual(step(work, 1, open: false), "virtual:\(waiting)")
        XCTAssertEqual(step(work, -1, open: false), "inbox")
        // From a view folded away, on from its button; from the brief up, nowhere.
        let view = try XCTUnwrap(entries.first { $0.id == "view:v1" }?.item?.place)
        XCTAssertEqual(step(view, -1, open: false), "documents")
        XCTAssertEqual(step(view, 1, open: true), "folder:\(Fixture.ada)\u{0}INBOX")
        XCTAssertNil(step(.desk(.brief), -1, open: false))
        XCTAssertEqual(step(nil, 1, open: false), "brief")
    }

    func testAnAccountFoldsAwayAndItsLabelsOpen() async {
        let mailbox = await mailbox()
        let closed = RailPlaces.entries(mailbox, desk: DeskRail(), closed: [Fixture.ada])
        XCTAssertTrue(closed.contains { $0.id == "account:\(Fixture.ada)" })
        XCTAssertFalse(closed.contains { $0.id.hasPrefix("folder:\(Fixture.ada)") })
        XCTAssertFalse(closed.contains { $0.id.hasPrefix("label:") })
        let labels = RailPlaces.entries(mailbox, desk: DeskRail(), labelsOpen: [Fixture.work])
        let projects = labels.first { $0.id == "label:\(Fixture.work)\u{0}Projects" }?.item
        XCTAssertEqual(projects?.count, .plain(12))
        XCTAssertEqual(projects?.icon, .tag)
    }

    func testFoldersTakeTheirPartsIcons() {
        XCTAssertEqual(RailPlaces.icon("inbox"), .inbox)
        XCTAssertEqual(RailPlaces.icon("sent"), .send)
        XCTAssertEqual(RailPlaces.icon("drafts"), .file)
        XCTAssertEqual(RailPlaces.icon("all"), .archive)
        XCTAssertEqual(RailPlaces.icon("junk"), .ban)
        XCTAssertEqual(RailPlaces.icon("custom"), .folder)
    }

    func testTheAccountsMenuOffersEveryAccountAndGoesByTheKeys() async throws {
        let mailbox = await mailbox()
        let menu = RailMenuModel()
        var chosen: [RailMenuChoice] = []
        menu.offer(mailbox: mailbox, current: Fixture.work, colours: { _ in [] }) { chosen.append($0) }
        XCTAssertEqual(menu.lines.map(\.choice), [.all, .account(Fixture.ada), .account(Fixture.work), .add])
        XCTAssertEqual(menu.lines.filter(\.chosen).map(\.choice), [.account(Fixture.work)])
        XCTAssertEqual(RailMenuModel.together(3), "3 accounts, one inbox")
        func key(_ code: UInt16) throws -> NSEvent {
            try XCTUnwrap(NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: [], timestamp: 0,
                                           windowNumber: 0, context: nil, characters: "",
                                           charactersIgnoringModifiers: "", isARepeat: false, keyCode: code))
        }
        XCTAssertTrue(menu.key(try key(125)))
        XCTAssertTrue(menu.key(try key(125)))
        XCTAssertEqual(menu.at, 1)
        XCTAssertTrue(menu.key(try key(36)))
        XCTAssertEqual(chosen, [.account(Fixture.ada)])
        XCTAssertFalse(menu.key(try key(0)), "a letter isn't the menu's")
    }

    /// The accounts' button names whose mail the list shows: beside the brief, the Inbox the list still holds, one
    /// account's or every account's, so it never says every account over one account's rows.
    func testTheAccountsButtonNamesTheAccountTheListShows() async {
        let app = MailApp()
        app.mailbox.wire = Fixture.server().wire
        await app.mailbox.refresh()
        app.place = .folder(account: Fixture.work, folder: "INBOX")
        XCTAssertEqual(RailAccountButton.place(of: app)?.account, Fixture.work)
        app.place = .desk(.brief)
        XCTAssertEqual(app.listed, .folder(account: Fixture.work, folder: "INBOX"), "the list keeps the Inbox's rows")
        XCTAssertEqual(RailAccountButton.place(of: app)?.account, Fixture.work, "and the button names its account")
        app.place = .allInboxes
        app.place = .desk(.brief)
        XCTAssertNil(RailAccountButton.place(of: app)?.account, "beside every account's Inbox, every account")
    }
}
