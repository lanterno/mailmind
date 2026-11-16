import Foundation
import XCTest

/// What is failing, in a person's words: a sync's error kind as a sentence with what to do next, and the background
/// work that isn't going through as one line in the status menu.
@MainActor
final class StatusProblemsTests: XCTestCase {
    func testEachSyncErrorKindIsASentenceWithWhatToDoNext() {
        let next = [
            "AUTH": "Choose Edit and type it again", "TIMEOUT": "check your internet connection",
            "NETWORK": "server’s name and port", "UIDVALIDITY": "Nothing needs doing", "TLS_PIN": "Trust it",
            "IMAP": "Copy Diagnostics", "UNKNOWN": "Copy Diagnostics", "SOMETHING_NEW": "Choose Retry",
        ]
        for (kind, step) in next {
            let said = StatusWords.syncProblem(kind)
            XCTAssertTrue(said.contains(step), "\(kind): \(said)")
            XCTAssertFalse(said.contains(kind), "\(kind) is said in words, not as its code: \(said)")
            XCTAssertTrue(said.hasSuffix("."), "a whole sentence: \(said)")
        }
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
