import Foundation
import Observation
import XCTest

/// The window's list keeps only the pages near the screen, reads again what it holds on a refresh, keeps the chosen
/// copy's row by its name, and says when a page couldn't be read.
@MainActor
final class MailListTests: XCTestCase {
    func testTheFirstPageCountsTheWholeList() async {
        let folder = Folder(count: 1000)
        let list = MailListModel(load: folder.load)
        list.start()
        await until { list.total == 1000 }
        XCTAssertEqual(list.row(at: 0)?.subject, "Message 0")
        XCTAssertEqual(list.row(at: 199)?.subject, "Message 199")
        XCTAssertNil(list.row(at: 200), "only the first page was asked for")
        XCTAssertNotNil(list.firstRowsAt)
        let asked = await folder.asked
        XCTAssertEqual(asked, [0])
    }

    func testScrollingKeepsThePagesNearTheScreenAndLetsTheRestGo() async {
        let folder = Folder(count: 1000)
        let list = MailListModel(load: folder.load)
        list.start()
        await until { list.total == 1000 }
        list.setVisible(600..<620)
        await until { list.row(at: 400) != nil && list.row(at: 999) != nil }
        XCTAssertNil(list.row(at: 0), "the first page is far from the screen")
        XCTAssertEqual(list.row(at: 610)?.subject, "Message 610")
        let asked = await folder.asked
        XCTAssertEqual(asked.sorted(), [0, 400, 600, 800], "the pages on screen and one on either side")
    }

    func testARefreshReadsAgainWhatIsHeldAndRedrawsWhatChanged() async {
        let folder = Folder(count: 300)
        let display = Recorder()
        let list = MailListModel(load: folder.load)
        list.display = display
        list.start()
        await until { list.total == 300 }
        await folder.rename(5, "Plan, again")
        list.refresh()
        await until { list.row(at: 5)?.subject == "Plan, again" }
        XCTAssertEqual(display.changes.last, IndexSet(integer: 5), "only the row that changed is redrawn")
    }

    func testTheChosenCopyIsFoundByItsNameWhereverItMoved() async {
        let folder = Folder(count: 50)
        let selection = Selection()
        let list = MailListModel(selection: selection, load: folder.load)
        list.start()
        await until { list.total == 50 }
        selection.copy = list.copy(at: 7).map(MailCopy.init)
        XCTAssertEqual(list.chosenPosition, 7)
        await folder.arrive(2)
        list.refresh()
        await until { list.total == 52 }
        XCTAssertEqual(list.chosenPosition, 9, "two new messages above it moved it down")
    }

    /// A list opened under a message already open finds where it stands once its first page comes, and what shows
    /// that place ("3 of 120") hears of it then, though the copy chosen never changed.
    func testWhereTheChosenCopyStandsIsToldAgainOnceItsPageComes() async {
        let folder = Folder(count: 50)
        let selection = Selection()
        selection.copy = MailCopy(Folder.row(7))
        let list = MailListModel(selection: selection, load: folder.load)
        let told = Told()
        withObservationTracking { _ = list.chosenPosition } onChange: { told.yes() }
        XCTAssertNil(list.chosenPosition, "no page yet")
        list.start()
        await until { list.total == 50 }
        XCTAssertTrue(told.was, "the page's coming is heard")
        XCTAssertEqual(list.chosenPosition, 7)
    }

    func testAPageThatCouldntBeReadSaysSo() async {
        let folder = Folder(count: 10, failing: true)
        let list = MailListModel(load: folder.load)
        list.start()
        await until { list.problem != nil }
        XCTAssertEqual(list.problem, "Refused here.")
        XCTAssertEqual(list.total, 0)
    }

    func testARowIsDrawnAsTheServerNamesItsSender() {
        var row = Folder.row(1)
        XCTAssertEqual(row.drawn.sender, "Sender 1")
        row.fromName = ""
        XCTAssertEqual(row.drawn.sender, "sender1@example.test", "no name: the address")
        XCTAssertTrue(row.drawn.unread)
    }

    /// Waits on the main actor, without blocking it, for what a page's arrival does.
    private func until(_ done: @MainActor () -> Bool, file: StaticString = #filePath, line: UInt = #line) async {
        let started = ContinuousClock.now
        while !done() {
            guard ContinuousClock.now - started < .seconds(5) else {
                XCTFail("still waiting after five seconds", file: file, line: line)
                return
            }
            try? await Task.sleep(for: .milliseconds(2))
        }
    }
}

/// A folder of fictional mail as `/api/messages` pages it, newest first.
private actor Folder {
    private var rows: [API.FolderRow]
    private let failing: Bool
    private(set) var asked: [Int] = []

    init(count: Int, failing: Bool = false) {
        rows = (0..<count).map(Self.row)
        self.failing = failing
    }

    nonisolated var load: MailListModel.Loader {
        { offset, limit in try await self.page(offset, limit) }
    }

    func page(_ offset: Int, _ limit: Int) throws -> MailListModel.Page {
        asked.append(offset)
        if failing {
            throw Problem(CoreAnswer(status: 400, contentType: "application/problem+json", headers: [],
                                     body: Data(#"{"type": "/problems/refused", "detail": "Refused here."}"#.utf8)))
        }
        return .init(total: rows.count, rows: Array(rows.dropFirst(offset).prefix(limit)))
    }

    func rename(_ position: Int, _ subject: String) {
        rows[position].subject = subject
    }

    /// New mail at the top, as a sync brings it.
    func arrive(_ count: Int) {
        rows.insert(contentsOf: (0..<count).map { Self.row(10_000 + $0) }, at: 0)
    }

    static func row(_ n: Int) -> API.FolderRow {
        API.FolderRow(account: "ada@example.test", action: "", commitmentWorthy: false, flagged: false, folder: "INBOX",
                      fromAddr: "sender\(n)@example.test", fromName: "Sender \(n)", hasAttach: false, isJunk: false,
                      key: n, needsYou: false, replyWorthy: false, safeToFile: false, seen: n % 2 == 0,
                      sentTo: [], sentToMore: 0, snippet: "",
                      subject: "Message \(n)", summary: "", ts: 1_793_620_800 - n * 60, uid: n + 1, when: "")
    }
}

/// Whether an observed value changed, told from wherever the change was made.
private nonisolated final class Told: @unchecked Sendable {
    private let lock = NSLock()
    private var said = false

    var was: Bool { lock.withLock { said } }

    func yes() { lock.withLock { said = true } }
}

@MainActor
private final class Recorder: ListDisplay {
    private(set) var changes: [IndexSet] = []

    func rowsChanged(_ positions: IndexSet, totalChanged: Bool) {
        changes.append(positions)
    }
}

/// What reads the window's list again. An Inbox's first scan says no new-mail, so its mail shows by the end of the sync
/// that stored its newest, and by each step of its older mail, never by waiting for new-mail.
@MainActor
final class MailAppRefreshTests: XCTestCase {
    private func event(_ name: String, _ data: String = "{}") -> CoreEvent {
        CoreEvent(id: "1", name: name, data: data)
    }

    func testAFirstScansEndAndEachStepOfItsOlderMailReadTheListAgain() {
        XCTAssertTrue(MailApp.refreshesTheList(event("sync-done", #"{"account": "ada@example.test", "kept": 500, "junk": 0}"#)))
        XCTAssertTrue(MailApp.refreshesTheList(event("folder-synced",
                                                     #"{"account": "ada@example.test", "folder": "INBOX", "ok": true, "new": 500}"#)))
        XCTAssertTrue(MailApp.refreshesTheList(event("new-mail")))
    }

    func testAFolderReadThatFailedOrAnEventAboutNoMailLeavesTheListAsItIs() {
        XCTAssertFalse(MailApp.refreshesTheList(event("folder-synced", #"{"folder": "INBOX", "ok": false}"#)),
                       "reading it again would read the folder again, for as long as its server says no")
        XCTAssertFalse(MailApp.refreshesTheList(event("outbox")))
        XCTAssertFalse(MailApp.refreshesTheList(event("hello")))
    }
}
