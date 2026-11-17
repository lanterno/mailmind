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
        XCTAssertEqual(why, """
            MailMind's key doesn't open this Mac's copy of your mail, so the copy can't be read. Start Fresh makes a \
            new copy from your mail's servers.
            """)
        try StartFresh.move(in: try XCTUnwrap(app.folder))
        await app.startAgain()
        XCTAssertEqual(app.stage, .open, "a new database, under the key there is")
        await app.core?.close()
        let kept = try manager.contentsOfDirectory(atPath: folder.appending(path: "backups").path(percentEncoded: false))
        XCTAssertTrue(kept.contains { $0.hasSuffix(".db") }, "the old one is kept: \(kept)")
    }

    /// The core's words for each failure it can meet as it opens (mm-server's `database_version`), as it says them.
    private static let said = (
        notKept: "MailMind's key to this Mac's copy of your mail isn't in your Keychain, so the copy can't be read. Start Fresh makes a new copy from your mail's servers.",
        notGiven: "Your Keychain didn't give MailMind the key to this Mac's copy of your mail. Unlock your login Keychain when macOS asks, then open MailMind again.",
        notTaken: "Your Keychain wouldn't keep MailMind's key, so no copy of your mail was made on this Mac. Unlock your login Keychain when macOS asks, then open MailMind again.",
        older: "This Mac's copy of your mail is at version 40, older than version 51, the oldest this MailMind can upgrade, so it was left as it is. Start Fresh makes a new copy from your mail's servers.",
        newer: "This Mac's copy of your mail is at version 99, made by a newer MailMind than this one, which reads up to version 60: install the newest MailMind to open it.",
        keyDoesNotFit: "MailMind's key doesn't open this Mac's copy of your mail, so the copy can't be read. Start Fresh makes a new copy from your mail's servers.",
        storage: "MailMind can't make or read this Mac's copy of your mail. Check that your Mac's disk has room, then open MailMind again.",
        failed: "The upgrade of this Mac's copy of your mail to version 60 failed, and the copy is as it was before. Open MailMind again to try it again."
    )

    func testStartFreshIsOfferedOnlyWhereANewCopyFixesWhatWentWrong() {
        for fixed in [Self.said.notKept, Self.said.older, Self.said.keyDoesNotFit] {
            XCTAssertTrue(LaunchFailure.startsFresh(fixed), fixed)
            XCTAssertFalse(LaunchFailure.triesAgain(fixed), "opening again changes nothing: \(fixed)")
        }
        for keychain in [Self.said.notGiven, Self.said.notTaken] {
            XCTAssertFalse(LaunchFailure.startsFresh(keychain), "a locked Keychain isn't fixed by a new copy: \(keychain)")
            XCTAssertTrue(LaunchFailure.triesAgain(keychain), "unlocked, opening again works: \(keychain)")
        }
        XCTAssertFalse(LaunchFailure.startsFresh(Self.said.newer), "a newer MailMind's copy wants that MailMind")
        XCTAssertFalse(LaunchFailure.triesAgain(Self.said.newer), "opening this MailMind again changes nothing")
        for again in [Self.said.storage, Self.said.failed, MailApp.unexpected] {
            XCTAssertTrue(LaunchFailure.triesAgain(again), again)
        }
        XCTAssertFalse(LaunchFailure.safe(startsFresh: false).localizedCaseInsensitiveContains("start fresh"),
                       "where it fixes nothing, nothing invites it")
    }

    /// Where no new copy is offered the core may have said no copy was made (a Keychain that wouldn't keep the key),
    /// so the words below it don't speak of one.
    func testWhereNoNewCopyIsOfferedTheWordsNameNoCopyOnThisMac() {
        XCTAssertTrue(Self.said.notTaken.contains("no copy of your mail was made"))
        XCTAssertFalse(LaunchFailure.startsFresh(Self.said.notTaken))
        XCTAssertEqual(LaunchFailure.safe(startsFresh: false), """
            Your mail on its servers is untouched, and so is anything MailMind keeps on this Mac. Do what the words \
            above say; if that doesn’t help, copy the details and send them to whoever gave you MailMind.
            """)
        XCTAssertFalse(LaunchFailure.safe(startsFresh: false).contains("the copy"),
                       "no copy is named beside words that say none was made")
    }

    func testAKeychainThatWontGiveTheKeyOffersToOpenAgainNotToStartFresh() async throws {
        // The keychain's stand-in, unreadable as a locked Keychain is: the core can't have the key.
        let standIn = folder.appending(path: "keychain.json")
        try Data("locked".utf8).write(to: standIn)
        let home = CoreHome(folder: folder.path(percentEncoded: false), variables: ["MAILMIND_NOSYNC": "1"],
                            standIn: standIn.path(percentEncoded: false))
        let app = MailApp(home: { home }, open: { try await Core.open($0) })
        await app.start()
        guard case .failed(let why) = app.stage else { return XCTFail("it opened: \(app.stage)") }
        XCTAssertTrue(why.contains("Unlock your login Keychain"), why)
        XCTAssertFalse(LaunchFailure.startsFresh(why), "no new copy is offered for a Keychain that didn't answer")
        XCTAssertTrue(LaunchFailure.triesAgain(why))
        // Unlocked, Try Again opens it.
        try manager.removeItem(at: standIn)
        await app.startAgain()
        XCTAssertEqual(app.stage, .open)
        await app.core?.close()
    }

    func testAPanicInTheCoreSaysPlainWordsNeverItsMessage() async {
        /// What a panic's message can carry across (UniFFI's `rustPanic`): words that quote mail.
        struct Panicked: LocalizedError {
            var errorDescription: String? { "byte index 3 is not a char boundary; it is inside 'é' of `Dear ada@example.test`" }
        }
        let home = CoreHome(folder: folder.path(percentEncoded: false), variables: [:], standIn: nil)
        let app = MailApp(home: { home }, open: { _ in throw Panicked() })
        await app.start()
        XCTAssertEqual(app.stage, .failed(MailApp.unexpected))
        XCTAssertEqual(MailApp.words(Panicked()), MailApp.unexpected)
        for quoted in ["example.test", "char boundary", "Dear"] {
            XCTAssertFalse(MailApp.unexpected.contains(quoted))
        }
        XCTAssertTrue(LaunchFailure.triesAgain(MailApp.unexpected), "it offers to open again")
    }

    func testTheWordsSayTheMailIsSafeInAPersonsWords() {
        XCTAssertTrue(LaunchFailure.safe(startsFresh: true).contains("Your mail on its servers is untouched"))
        XCTAssertTrue(LaunchFailure.safe(startsFresh: false).contains("Your mail on its servers is untouched"))
        XCTAssertTrue(LaunchFailure.fresh.contains("backups folder"))
        for words in [LaunchFailure.safe(startsFresh: true), LaunchFailure.safe(startsFresh: false), LaunchFailure.fresh] {
            for developers in ["Python", "just run", "`", "database key"] {
                XCTAssertFalse(words.contains(developers), "\(developers) in \(words)")
            }
        }
    }
}
