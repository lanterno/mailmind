import Foundation
import XCTest

/// What the desk's screens in Meadow's look work out before they draw: the brief's greeting by the light and the
/// hour, its date line and when it was written, the mail its words name as links, its tallies and what is worth a
/// read; the paper trail's month against the one before, its bars and its facts; and the gate's queue, what it puts
/// off and remembers deciding, and how it reads a visitor's letter.
@MainActor
final class MeadowDeskTests: XCTestCase {
    private var english: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        return calendar
    }

    // MARK: The paper trail

    private func receipt(_ date: String, _ subject: String, account: String = "ada@example.test",
                         folder: String = "INBOX") -> API.SearchHit {
        API.SearchHit(account: account, commitmentWorthy: false, date: date, flagged: false, folder: folder,
                      fromAddr: "shop@example.test", fromName: "Shop", hasAttach: false, isJunk: false, key: 1,
                      needsYou: false, replyWorthy: false, safeToFile: true, seen: true, snippet: "", subject: subject,
                      uid: 1, when: date)
    }

    private var trail: [Ledger.Month] {
        Ledger.months([
            receipt("2026-11-13", "Your e-ticket — $98.50", folder: "Travel"),
            receipt("2026-11-11", "Invoice — $45.00", account: "work@example.test", folder: "Invoices"),
            receipt("2026-11-02", "Refund for a cancelled class — $20.00"),
            receipt("2026-10-28", "Your receipt — $18.20"),
            receipt("2026-10-03", "Your e-ticket — $250.20"),
            receipt("2026-09-21", "Your receipt — €30.00"),
            receipt("", "A receipt with no date — $5.00"),
        ])
    }

    func testAMonthsTotalIsSaidAgainstTheMonthBefore() throws {
        let us = Locale(identifier: "en_US")
        let november = try XCTUnwrap(Ledger.summary(trail, at: 0))
        XCTAssertEqual(november.total, 123.5, accuracy: 0.001)
        XCTAssertEqual([november.receipts, november.refunds], [2, 1])
        XCTAssertEqual(november.beforeMonth, "2026-10")
        XCTAssertEqual(Ledger.words(november, locale: us), "2 receipts · 1 refund · $144.90 less than October")
        // September's receipts are in euros, so October has nothing to be said against.
        XCTAssertEqual(Ledger.words(try XCTUnwrap(Ledger.summary(trail, at: 1)), locale: us), "2 receipts")
        // The month after September is mail with no date, which is no month before it.
        XCTAssertNil(try XCTUnwrap(Ledger.summary(trail, at: 2)).before)
        XCTAssertNil(Ledger.summary(trail, at: 9))

        let even = Ledger.months([receipt("2026-11-05", "$10.00"), receipt("2026-10-05", "$10.00")])
        XCTAssertEqual(Ledger.words(try XCTUnwrap(Ledger.summary(even, at: 0)), locale: us),
                       "1 receipt · the same as October")
        let more = Ledger.months([receipt("2026-11-05", "$30.00"), receipt("2026-10-05", "$10.00")])
        XCTAssertEqual(Ledger.words(try XCTUnwrap(Ledger.summary(more, at: 0)), locale: us),
                       "1 receipt · $20.00 more than October")
    }

    func testAMonthsBarsRunOldestFirstAndEndAtTheMonth() {
        let bars = Ledger.bars(trail, at: 0)
        XCTAssertEqual(bars.count, 3)
        XCTAssertEqual(bars[0], 0, accuracy: 0.001, "September's total is in euros")
        XCTAssertEqual(bars[1], 1, accuracy: 0.001)
        XCTAssertEqual(bars[2], 123.5 / 268.4, accuracy: 0.001)
        XCTAssertEqual(Ledger.bars(trail, at: 1, count: 1), [1])
    }

    func testAMonthsFactsByAccountAndByFolder() {
        let november = trail[0]
        XCTAssertEqual(Ledger.byAccount(november).map { "\($0.account) \($0.currency)\($0.value)" },
                       ["ada@example.test $78.5", "work@example.test $45.0"])
        XCTAssertEqual(Ledger.byFolder(november).map { "\($0.folder) \($0.count)" },
                       ["Travel 1", "Invoices 1", "INBOX 1"])
        XCTAssertEqual(Ledger.monthOnly("2026-10"), "October")
        XCTAssertEqual(Ledger.monthOnly(""), "Undated")
    }
}
