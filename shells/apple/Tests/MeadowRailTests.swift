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
        let entries = RailPlaces.entries(mailbox, desk: DeskRail(brief: true, needs: 3, gates: [
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
}
