import Foundation
import XCTest

/// What is failing, in a person's words: a sync's error kind as a sentence with what to do next.
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
}
