import Foundation
import XCTest

/// Help › Copy Diagnostics: what the owner needs, and never an address, a subject, a name or a secret, whatever the
/// log and the routes hold.
@MainActor
final class DiagnosticsTests: XCTestCase {
    private let about = Diagnostics.About(version: "0.3.0", build: "0.3.0", arch: "arm64",
                                          system: "Version 26.1 (Build 25B78)", chip: "Apple M2", model: "Mac14,2")
    private let now = StatusFixtures.at("2026-11-16T12:00:00Z")

    /// A fictional log as a careless line could write it: addresses bare, in angle brackets with a name, quoted,
    /// URL-encoded, as a field's value and as a key; and a line that isn't the log's own.
    private let log = """
        {"ts": "2026-11-16T11:58:00.000+00:00", "level": "INFO", "logger": "sync", "msg": "synced 12 new"}
        {"ts": "2026-11-16T11:58:30.000+00:00", "level": "WARNING", "logger": "sync", "msg": "login failed for ada@example.test", "error": "LoginRejected"}
        {"ts": "2026-11-16T11:59:00.000+00:00", "level": "ERROR", "logger": "smtp", "msg": "refused Ada Lovelace <ada.lovelace@example.test> and \\"Grace Hopper\\" <grace@example.test>", "error": "Refused"}
        {"ts": "2026-11-16T11:59:10.000+00:00", "level": "WARNING", "logger": "http", "msg": "GET /api/messages?account=bob%40example.test", "path": "bob%40example.test", "error": "Timeout"}
        Subject: Our secret plans, from carol@example.test
        {"ts": "2026-11-16T11:59:20.000+00:00", "level": "WARNING", "logger": "sync", "msg": "login failed for <withheld>", "error": "LoginRejected"}
        """

    private var read: Diagnostics.Read {
        var read = Diagnostics.Read()
        read.digest = StatusFixtures.digest(accounts: ["ada@example.test", "news@example.test"])
        read.accounts = [StatusFixtures.account("ada@example.test", alias: "Ada Lovelace", type: "gmail"),
                         StatusFixtures.account("news@example.test", style: "op", ref: "op://Private/news@example.test/password")]
        read.health = [
            StatusFixtures.standing("ada@example.test", ok: "2026-11-16T10:00:00Z", count: 14),
            StatusFixtures.standing("news@example.test", error: "LoginRejected", kind: "AUTH",
                                    errorAt: "2026-11-16T09:00:00Z", count: 3),
        ]
        read.glance = .object([
            "ok": .bool(true), "profile": .string("full"),
            "queues": .object(["outbox": .object(["queued": .number(1)]), "drafts_waiting": .number(0)]),
            "breakers": .array([.string("dan@example.test")]),
            "jobs": .array([]),
        ])
        read.jobs = .object(["jobs": .array([
            .object(["name": .string("sync"), "runs": .number(4), "failures": .number(0), "last_error": .null]),
            .object(["name": .string("draft-push"), "runs": .number(9), "failures": .number(2),
                     "last_error": .string("Timeout")]),
        ]), "by": .object(["erin@example.test": .number(1)])])
        return read
    }

    private func text(read: Diagnostics.Read? = nil, opened: Bool = true, failure: String? = nil) -> String {
        Diagnostics.text(about: about, read: read ?? self.read, opened: opened, failure: failure, log: log, now: now)
    }

    func testItHoldsNoAddressNameSubjectOrSecret() {
        let copied = text()
        for personal in ["@", "%40", "example.test/password", "Ada Lovelace", "Grace Hopper", "Lovelace", "Hopper",
                         "Subject", "secret plans", "carol", "op://", "Ada"] {
            XCTAssertFalse(copied.contains(personal), "\(personal) was copied:\n\(copied)")
        }
    }

    func testItSaysWhatTheOwnerNeeds() {
        let copied = text()
        for needed in [
            "MailMind 0.3.0 (build 0.3.0), arm64", "Version 26.1 (Build 25B78) · Apple M2 · Mac14,2",
            "Profile: full", "AI: on · anthropic",
            "Account 1 · gmail · in own server:993 TLS · out own server:465 TLS · password: keychain",
            "synced 2h ago, 14 syncs",
            "Account 2 · imap", "password: op", "last error AUTH (LoginRejected) 3h ago",
            "account 2: AUTH (LoginRejected), 3h ago", "job draft-push: Timeout",
            "in the log: LoginRejected × 2", "in the log: Refused × 1", "in the log: Timeout × 1",
            #""queues":{"drafts_waiting":0,"outbox":{"queued":1}}"#, #""name":"draft-push""#,
            "[sync] synced 12 new",
        ] {
            XCTAssertTrue(copied.contains(needed), "\(needed) is missing:\n\(copied)")
        }
        XCTAssertFalse(copied.contains(#""jobs":[]"#), "the jobs are listed once, under their own heading")
    }

    func testAServerIsNamedOnlyWhenItIsAProvidersOrThisMacs() {
        for (host, named) in [
            ("imap.gmail.com", "imap.gmail.com"), ("smtp.gmail.com", "smtp.gmail.com"),
            ("imap.mail.me.com", "imap.mail.me.com"), ("smtp.mail.yahoo.com", "smtp.mail.yahoo.com"),
            ("imap.aol.com", "imap.aol.com"), ("imap.fastmail.com", "imap.fastmail.com"),
            ("outlook.office365.com", "outlook.office365.com"), ("smtp-mail.outlook.com", "smtp-mail.outlook.com"),
            (" IMAP.Gmail.COM. ", "imap.gmail.com"), ("127.0.0.1", "127.0.0.1"), ("localhost", "localhost"),
            ("mail.ada-family.example.test", "own server"), ("imap.gmail.com.ada-family.example.test", "own server"),
            ("notgmail.com", "own server"), ("192.168.1.20", "own server"), ("mx.lovelace-works.example.test", "own server"),
        ] {
            XCTAssertEqual(Diagnostics.server(host), named, host)
        }
    }

    func testAServerOfAPersonsOwnIsNeverCopied() {
        var read = read
        read.accounts = [
            StatusFixtures.account("ada@example.test", host: "mail.ada-family.example.test",
                                   smtpHost: "smtp.ada-family.example.test"),
            StatusFixtures.account("ada@example.test", type: "gmail", host: "imap.gmail.com", smtpHost: "smtp.gmail.com"),
            StatusFixtures.account("bea@example.test", type: "proton", host: "127.0.0.1", smtpHost: "127.0.0.1"),
        ]
        let copied = text(read: read)
        XCTAssertFalse(copied.contains("ada-family"), "a server of a person's own was copied:\n\(copied)")
        for needed in [
            "Account 1 · imap · in own server:993 TLS · out own server:465 TLS",
            "Account 2 · gmail · in imap.gmail.com:993 TLS · out smtp.gmail.com:465 TLS",
            "Account 3 · proton · in 127.0.0.1:993 TLS · out 127.0.0.1:465 TLS",
        ] {
            XCTAssertTrue(copied.contains(needed), "\(needed) is missing:\n\(copied)")
        }
    }

    func testALineThatIsntTheLogsOwnIsLeftOut() {
        let lines = Diagnostics.logLines(log)
        XCTAssertEqual(lines.count, 5, "the line that isn't JSON can't be told safe")
        XCTAssertFalse(lines.joined().contains("Subject"))
    }

    func testOnlyTheLogsLastLinesGo() {
        let many = (1...100).map { #"{"ts": "t\#($0)", "level": "INFO", "logger": "jobs", "msg": "round \#($0)"}"# }
        let lines = Diagnostics.logLines(many.joined(separator: "\n"))
        XCTAssertEqual(lines.count, Diagnostics.logLines)
        XCTAssertEqual(lines.last, "t100 INFO [jobs] round 100")
    }

    func testAKeyOrAValueHoldingAnAddressIsTakenOut() {
        XCTAssertEqual(Diagnostics.compact(.object(["frank@example.test": .array([.string("x frank%40example.test")])])),
                       #"{"<address>":["x <address>"]}"#)
        XCTAssertEqual(Diagnostics.redacted("to \"Ada L.\" <ada@example.test>, mailto:bob@example.test; then"),
                       "to <address>, mailto:<address>; then")
    }

    func testWithNoCoreItSaysWhyAndStillCarriesTheLog() {
        let copied = text(read: Diagnostics.Read(), opened: false, failure: "the database key doesn't open the database")
        XCTAssertTrue(copied.contains("MailMind didn’t open: the database key doesn't open the database"), copied)
        XCTAssertTrue(copied.contains("[smtp] refused <address> and <address>: Refused"), copied)
        XCTAssertFalse(copied.contains("Health ("), "nothing is said of what couldn't be asked")
        XCTAssertFalse(copied.contains("@"))
    }

    func testTheLogIsReadFromItsEndAndItsFile() throws {
        let folder = FileManager.default.temporaryDirectory.appending(path: "mailmind-diagnostics-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        let line = #"{"ts": "t", "level": "INFO", "logger": "jobs", "msg": "a round of the background's work"}"#
        let lines = Array(repeating: line, count: 3000)
        try (lines.joined(separator: "\n") + "\n").write(to: folder.appending(path: "mailmind.log"), atomically: true,
                                                          encoding: .utf8)
        let tail = try XCTUnwrap(Diagnostics.logTail(at: folder.appending(path: "mailmind.log")))
        XCTAssertLessThanOrEqual(tail.utf8.count, Diagnostics.logBytes)
        XCTAssertTrue(tail.hasPrefix("{"), "it starts at a whole line")
        XCTAssertEqual(Diagnostics.logLines(tail).count, Diagnostics.logLines)
        XCTAssertNil(Diagnostics.logTail(at: folder.appending(path: "none.log")))
    }

    func testFromACoreItReadsTheRoutesAndTheLog() async throws {
        let folder = FileManager.default.temporaryDirectory.appending(path: "mailmind-diagnostics-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        let core = try await Core.open(CoreHome(
            folder: folder.path(percentEncoded: false),
            variables: ["MAILMIND_DB_KEY": "2b4d6f8a0c1e3b5d7f9a2c4e6b8d0f1a8f3a1c5e7b9d2f4a6c8e0b1d3f5a7c9e",
                        "MAILMIND_NOSYNC": "1"],
            standIn: folder.appending(path: "keychain.json").path(percentEncoded: false)))
        let copied = await Diagnostics.gather(core: core, folder: folder, failure: nil, about: about, now: now)
        await core.close()
        XCTAssertTrue(copied.contains("Profile: read-only"), "as MAILMIND_NOSYNC runs it:\n\(copied)")
        XCTAssertTrue(copied.contains("Accounts (0)"), copied)
        XCTAssertTrue(copied.contains(#""ok":true"#), "the core's own account of itself:\n\(copied)")
        XCTAssertTrue(copied.contains("Jobs (/api/debug/jobs)"), copied)
        XCTAssertFalse(copied.contains("couldn’t be read"), copied)
    }

    func testReportingSaysWhereToSendIt() {
        XCTAssertTrue(Diagnostics.reportWords.contains("whoever gave you MailMind"))
        XCTAssertEqual(Diagnostics.issues.host(), "github.com")
    }
}
