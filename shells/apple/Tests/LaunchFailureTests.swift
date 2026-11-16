import Foundation
import XCTest

/// When MailMind didn't open: Start Fresh moves the database aside into `backups/` and opens a new one, the accounts
/// and the log left where they are, and the words say the mail on the servers is untouched.
@MainActor
final class LaunchFailureTests: XCTestCase {
    nonisolated(unsafe) private var folder: URL!
    nonisolated(unsafe) private let manager = FileManager.default

    override func setUpWithError() throws {
        folder = manager.temporaryDirectory.appending(path: "mailmind-start-fresh-\(UUID().uuidString)")
        try manager.createDirectory(at: folder, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? manager.removeItem(at: folder)
    }

    private func exists(_ name: String) -> Bool {
        manager.fileExists(atPath: folder.appending(path: name).path(percentEncoded: false))
    }

    private func write(_ names: String...) throws {
        for name in names { try Data(name.utf8).write(to: folder.appending(path: name)) }
    }

    func testTheDatabaseAndItsJournalMoveIntoBackupsAndNothingElse() throws {
        try write("mailmind.db", "mailmind.db-wal", "mailmind.db-shm", "mailmind.config.json", "mailmind.log")
        let now = StatusFixtures.at("2026-11-16T09:30:00Z")
        let moved = try XCTUnwrap(try StartFresh.move(in: folder, now: now))
        XCTAssertEqual(moved.lastPathComponent, "mailmind-before-starting-fresh-20261116T093000Z.db")
        for name in ["mailmind.db", "mailmind.db-wal", "mailmind.db-shm"] {
            XCTAssertFalse(exists(name), "\(name) moved")
        }
        for suffix in [".db", ".db-wal", ".db-shm"] {
            XCTAssertTrue(exists("backups/mailmind-before-starting-fresh-20261116T093000Z\(suffix)"))
        }
        XCTAssertTrue(exists("mailmind.config.json"), "the accounts stay set up")
        XCTAssertTrue(exists("mailmind.log"), "the log stays for the owner")
        let mode = try manager.attributesOfItem(atPath: folder.appending(path: "backups").path(percentEncoded: false))[.posixPermissions]
        XCTAssertEqual(mode as? Int, 0o700, "the backups are their owner's alone")
        XCTAssertFalse("mailmind-before-starting-fresh".hasPrefix("mailmind-v"),
                       "the core prunes only its own upgrade backups, never this")
    }

    func testAgainInTheSameSecondItTakesANameOfItsOwn() throws {
        let now = StatusFixtures.at("2026-11-16T09:30:00Z")
        try write("mailmind.db")
        try StartFresh.move(in: folder, now: now)
        try write("mailmind.db")
        let again = try XCTUnwrap(try StartFresh.move(in: folder, now: now))
        XCTAssertEqual(again.lastPathComponent, "mailmind-before-starting-fresh-20261116T093000Z-2.db")
        XCTAssertTrue(exists("backups/mailmind-before-starting-fresh-20261116T093000Z.db"), "the first is kept")
    }

    func testWithNoDatabaseNothingMoves() throws {
        try write("mailmind.config.json")
        XCTAssertNil(try StartFresh.move(in: folder))
        XCTAssertFalse(exists("backups"))
    }

    func testAfterStartingFreshACoreThatDidntOpenOpensOnANewDatabase() async throws {
        func home(_ key: String) -> CoreHome {
            CoreHome(folder: folder.path(percentEncoded: false),
                     variables: ["MAILMIND_DB_KEY": key, "MAILMIND_NOSYNC": "1"],
                     standIn: folder.appending(path: "keychain.json").path(percentEncoded: false))
        }
        let first = try await Core.open(home(String(repeating: "1a", count: 32)))
        await first.close()
        // The key that opened it is gone: this database can't be read again.
        let other = home(String(repeating: "2b", count: 32))
        let app = MailApp(home: { other }, open: { try await Core.open($0) })
        await app.start()
        guard case .failed(let why) = app.stage else { return XCTFail("it opened: \(app.stage)") }
        XCTAssertEqual(why, "the database key doesn't open the database")
        try StartFresh.move(in: try XCTUnwrap(app.folder))
        await app.startAgain()
        XCTAssertEqual(app.stage, .open, "a new database, under the key there is")
        await app.core?.close()
        let kept = try manager.contentsOfDirectory(atPath: folder.appending(path: "backups").path(percentEncoded: false))
        XCTAssertTrue(kept.contains { $0.hasSuffix(".db") }, "the old one is kept: \(kept)")
    }

    func testTheWordsSayTheMailIsSafeInAPersonsWords() {
        XCTAssertTrue(LaunchFailure.safe.contains("Your mail on its servers is untouched"))
        XCTAssertTrue(LaunchFailure.fresh.contains("backups folder"))
        for words in [LaunchFailure.safe, LaunchFailure.fresh] {
            for developers in ["Python", "just run", "`", "database key"] {
                XCTAssertFalse(words.contains(developers), "\(developers) in \(words)")
            }
        }
    }
}
