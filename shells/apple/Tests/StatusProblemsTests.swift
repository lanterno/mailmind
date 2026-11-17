import Foundation
import XCTest

/// What is failing, in a person's words: a sync's error kind as a sentence with what to do next, and the background
/// work that isn't going through as one line in the status menu.
@MainActor
final class StatusProblemsTests: XCTestCase {
    func testEachSyncErrorKindIsASentenceWithWhatToDoNext() {
        let next = [
            "AUTH": "change it where MailMind reads this account’s password, then choose Retry",
            "TIMEOUT": "check your internet connection", "NETWORK": "Check your internet connection, then choose Retry",
            "UIDVALIDITY": "Nothing needs doing", "TLS_PIN": "Trust it",
            "IMAP": "Copy Diagnostics", "UNKNOWN": "Copy Diagnostics", "SOMETHING_NEW": "Choose Retry",
        ]
        for (kind, step) in next {
            let said = StatusWords.syncProblem(kind)
            XCTAssertTrue(said.contains(step), "\(kind): \(said)")
            XCTAssertFalse(said.contains(kind), "\(kind) is said in words, not as its code: \(said)")
            XCTAssertTrue(said.hasSuffix("."), "a whole sentence: \(said)")
        }
    }

    func testAProtonAccountsTroubleIsItsBridgeAndAGmailAccountsIsItsAppPassword() {
        // Bridge closed: nothing listens on this Mac, so the connection is refused at once.
        let down = StatusWords.syncProblem("NETWORK", type: "proton", error: "ConnectionRefused")
        XCTAssertTrue(down.hasPrefix("Proton Bridge isn’t running"), down)
        XCTAssertTrue(down.contains("Start Proton Bridge"), down)
        XCTAssertFalse(down.contains("couldn’t reach the server"), "not the generic words: \(down)")
        XCTAssertFalse(down.contains("name and port"), "a Proton account has no server to edit: \(down)")
        XCTAssertEqual(StatusWords.syncHelp("NETWORK", type: "proton"), StatusWords.protonBridge)
        // Bridge open but too slow: something took the connection, so it is running, busy or stuck.
        let slow = StatusWords.syncProblem("TIMEOUT", type: "proton", error: "Timeout")
        XCTAssertEqual(slow, """
            Proton Bridge didn’t answer in time. If it is still starting or syncing, wait until it says it is \
            connected, then choose Retry; if it seems stuck, quit Bridge and open it again.
            """)
        XCTAssertFalse(slow.contains("isn’t running"), "Bridge is open in front of the person: \(slow)")
        XCTAssertFalse(slow.contains("Start Proton Bridge"), slow)
        XCTAssertEqual(StatusWords.syncHelp("TIMEOUT", type: "proton"), StatusWords.protonBridge)
        XCTAssertEqual(StatusWords.protonBridge.url, "https://proton.me/mail/bridge")
        XCTAssertTrue(StatusWords.syncProblem("AUTH", type: "proton").contains("the password Bridge shows"))

        let gmail = StatusWords.syncProblem("AUTH", type: "gmail", error: "LoginNeedsAppPassword")
        XCTAssertTrue(gmail.contains("Gmail needs an App Password, not your Google password"), gmail)
        XCTAssertEqual(StatusWords.syncHelp("AUTH", type: "gmail", error: "LoginNeedsAppPassword"), StatusWords.appPasswords)
        XCTAssertEqual(StatusWords.appPasswords.url, "https://myaccount.google.com/apppasswords")
        XCTAssertEqual(StatusWords.syncHelp("AUTH", type: "gmail", error: "LoginRefused"), StatusWords.appPasswords)
        let offline = StatusWords.syncProblem("NETWORK", type: "gmail", error: "ConnectionRefused")
        XCTAssertEqual(offline, "MailMind couldn’t reach Gmail. Check your internet connection, then choose Retry.")
        XCTAssertNil(StatusWords.syncHelp("NETWORK", type: "gmail"))
        // Google's other refusals aren't an App Password's: each says its own step.
        XCTAssertTrue(StatusWords.syncProblem("AUTH", type: "gmail", error: "LoginNeedsWebSignIn").contains("sign in on the web"))
        XCTAssertTrue(StatusWords.syncProblem("AUTH", type: "gmail", error: "LoginImapOff").contains("IMAP is switched off"))
        XCTAssertNil(StatusWords.syncHelp("AUTH", type: "gmail", error: "LoginNeedsWebSignIn"))

        // An account with servers of its own checks them, as before.
        XCTAssertTrue(StatusWords.syncProblem("NETWORK", type: "imap", fix: .server)
            .contains("its name and port under Edit Server"))
        XCTAssertNil(StatusWords.syncHelp("NETWORK", type: "imap"))
        XCTAssertNil(StatusWords.syncHelp("AUTH", type: "imap"))
    }

    /// A login held back after a refusal fails as LoginRefused whatever Google first said (IMAP off, sign in on the
    /// web), and so does a wrong App Password: the words for it, in the row and the banner, hold for each.
    func testAGmailLoginTurnedDownWithoutGooglesReasonSaysWhatHoldsForEveryReason() {
        for error in ["LoginRefused", nil] {
            let said = StatusWords.syncProblem("AUTH", type: "gmail", error: error, fix: .appPassword)
            XCTAssertEqual(said, """
                Gmail turned down the login: if you typed your Google password, make an App Password and choose \
                Enter App Password; if you already use one, sign in to Gmail in your browser and check that IMAP is \
                on, then choose Retry.
                """)
            XCTAssertFalse(said.contains("not your Google password"),
                           "someone who already uses an App Password isn't told they don't: \(said)")
            // One whose password MailMind reads from 1Password or a variable is changed there.
            let elsewhere = StatusWords.syncProblem("AUTH", type: "gmail", error: error)
            XCTAssertTrue(elsewhere.contains("put an App Password where it reads this account’s password"), elsewhere)
            XCTAssertFalse(elsewhere.contains("Enter App Password"), "no button to name: \(elsewhere)")
            XCTAssertEqual(StatusWords.syncHelp("AUTH", type: "gmail", error: error), StatusWords.appPasswords)
        }

        let held = API.AccountStanding(account: "cy@example.test", healthy: false, lastError: "LoginRefused",
                                       lastErrorKind: "AUTH", lastErrorTs: "2026-11-16T09:00:00Z", live: "",
                                       syncCount: 3)
        let banner = StatusWords.alert(["cy@example.test"], health: [held], type: { _ in "gmail" },
                                       name: StatusWords.short)
        XCTAssertEqual(banner.text, """
            cy can’t connect: Gmail turned down the login. If you typed your Google password, it needs an App Password.
            """)
        XCTAssertEqual(banner.link, StatusWords.appPasswords)
    }

    func testTheBannerSaysToStartBridgeOrMakeAnAppPassword() {
        func standing(_ account: String, _ kind: String, _ error: String) -> API.AccountStanding {
            API.AccountStanding(account: account, healthy: false, lastError: error, lastErrorKind: kind,
                                lastErrorTs: "2026-11-16T09:00:00Z", live: "", syncCount: 3)
        }
        let types = ["ada@example.test": "proton", "bo@example.test": "proton", "cy@example.test": "gmail",
                     "di@example.test": "imap", "ev@example.test": "proton", "fay@example.test": "proton"]
        let health = [standing("ada@example.test", "NETWORK", "ConnectionRefused"),
                      standing("bo@example.test", "NETWORK", "ConnectionRefused"),
                      standing("cy@example.test", "AUTH", "LoginNeedsAppPassword"),
                      standing("di@example.test", "NETWORK", "ConnectionRefused"),
                      standing("ev@example.test", "TIMEOUT", "Timeout"),
                      standing("fay@example.test", "TIMEOUT", "Timeout")]
        func alert(_ bad: [String]) -> (text: String, link: API.Link?) {
            StatusWords.alert(bad, health: health, type: { types[$0] }, name: StatusWords.short)
        }

        let bridge = alert(["ada@example.test", "bo@example.test"])
        XCTAssertEqual(bridge.text, "Proton Bridge isn’t running, so ada, bo can’t connect. Start Proton Bridge.")
        XCTAssertEqual(bridge.link, StatusWords.protonBridge)
        let slow = alert(["ev@example.test", "fay@example.test"])
        XCTAssertEqual(slow.text, """
            Proton Bridge didn’t answer in time, so ev, fay can’t connect. If it is still syncing, wait; if it seems \
            stuck, quit Bridge and open it again.
            """, "Bridge is open, so it isn't told to start")
        XCTAssertEqual(slow.link, StatusWords.protonBridge)
        let both = alert(["ada@example.test", "ev@example.test"])
        XCTAssertEqual(both.text, "2 accounts couldn’t connect (ada, ev).", "neither sentence holds for both")
        let gmail = alert(["cy@example.test"])
        XCTAssertEqual(gmail.text, "cy can’t connect: Gmail needs an App Password, not your Google password.")
        XCTAssertEqual(gmail.link, StatusWords.appPasswords)

        let other = alert(["di@example.test"])
        XCTAssertEqual(other.text, "1 account couldn’t connect (di).")
        XCTAssertNil(other.link)
        let mixed = alert(["ada@example.test", "di@example.test"])
        XCTAssertEqual(mixed.text, "2 accounts couldn’t connect (ada, di).", "Bridge isn't all that is wrong")
        XCTAssertNil(mixed.link)
    }

    func testOnlyAJobWhoseLastRoundFailedIsFailing() {
        func job(_ name: String, _ error: String?, failures: Int) -> API.Job {
            StatusFixtures.decode(API.Job.self, """
                {"name": "\(name)", "every": 60, "failures": \(failures), "running": false, "runs": 5,
                 "last_error": \(error.map { "\"\($0)\"" } ?? "null"), "last_started": null, "last_took": null,
                 "next_at": null}
                """)
        }
        let jobs = [job("sync", nil, failures: 0), job("draft-push", "Timeout", failures: 3),
                    job("expire", nil, failures: 2), job("backfill", "Refused", failures: 1)]
        XCTAssertEqual(StatusWords.failing(jobs), ["draft-push", "backfill"],
                       "a job that failed once and went through since isn't failing")
    }

    func testFailingWorkIsOneLineInWords() {
        XCTAssertNil(StatusWords.failingLine([]))
        let line = StatusWords.failingLine(["draft-push", "backfill", "new-job"])
        XCTAssertEqual(line, """
            Some background work isn’t going through: saving drafts to the server, fetching older messages’ text \
            and new-job. Help › Copy Diagnostics says more.
            """, "a job this build has no words for keeps its name")
        XCTAssertEqual(StatusWords.failingLine(["expire"]),
                       "Some background work isn’t going through: expiry rules. Help › Copy Diagnostics says more.")
    }

    func testEveryJobTheServersRunIsNamedInWordsNeverByItsName() {
        // Both servers' jobs (mm-server's workers and background, mailmind/bootstrap/background.py), the first scan's
        // read of older mail among them.
        let jobs = ["sync", "snooze", "reconcile", "draft-push", "expire", "forget", "backfill", "older-mail", "assess",
                    "draft-ahead", "brief-ahead", "review-ahead", "tidy"]
        for job in jobs {
            XCTAssertNotEqual(StatusWords.jobWords(job), job, "\(job) is said in words")
        }
        XCTAssertEqual(StatusWords.failingLine(["older-mail"]), """
            Some background work isn’t going through: reading your older mail. Help › Copy Diagnostics says more.
            """)
    }

    func testTheStatusReadsWhichJobsFailFromTheCore() async throws {
        let folder = FileManager.default.temporaryDirectory.appending(path: "mailmind-jobs-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        let core = try await Core.open(CoreHome(
            folder: folder.path(percentEncoded: false),
            variables: ["MAILMIND_DB_KEY": String(repeating: "3c", count: 32), "MAILMIND_NOSYNC": "1"],
            standIn: folder.appending(path: "keychain.json").path(percentEncoded: false)))
        let status = AppStatus(newMail: NewMailAlerts(notifier: SystemNotifier.Silent(), isActive: { true },
                                                      badge: { _ in }, announce: { _ in }))
        await status.attach(core)
        await status.readJobs()
        XCTAssertEqual(status.failingJobs, [], "a fresh home's jobs have failed in nothing")
        status.detach()
        await core.close()
    }
}
