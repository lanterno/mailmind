import XCTest

/// MailMind Dev's driver starts only where it can't reach a person's mail, Keychain or time
/// (`Sources/Dev/Driver/DriverSafety.swift`, docs/test-architecture.md section 6).
final class DriverSafetyTests: XCTestCase {
    private let temporary = NSTemporaryDirectory()
    private var safe: [String: String] {
        ["MAILMIND_HOME": (temporary as NSString).appendingPathComponent("mm-home-x/home"),
         "MAILMIND_TEST_KEYCHAIN": "/tmp/keychain.json", "MAILMIND_TEST_CLOCK": "paused",
         "MAILMIND_DB_KEY": "5e"]
    }

    private func refusal(_ environment: [String: String], identifier: String? = DriverSafety.identity) -> String? {
        DriverSafety.refusal(environment, identifier: identifier, temporary: temporary)
    }

    func testItStartsInMailMindDevOnATempHomeWithTheStandInsAndAPausedClock() {
        XCTAssertNil(refusal(safe))
    }

    func testItRefusesThePersonsMailMind() throws {
        let said = try XCTUnwrap(refusal(safe, identifier: "io.github.lanterno.mailmind"))
        XCTAssertTrue(said.contains("only in MailMind Dev"), said)
        XCTAssertNotNil(refusal(safe, identifier: nil))
    }

    func testItRefusesAHomeOutsideTheTempFolderOrNone() {
        var outside = safe
        outside["MAILMIND_HOME"] = NSHomeDirectory() + "/Library/Application Support/io.github.lanterno.mailmind"
        XCTAssertNotNil(refusal(outside))
        var climbing = safe
        climbing["MAILMIND_HOME"] = temporary + "/../../Users/someone"
        XCTAssertNotNil(refusal(climbing), "a path that climbs out isn't inside")
        var itself = safe
        itself["MAILMIND_HOME"] = temporary
        XCTAssertNotNil(refusal(itself), "the temp folder itself is no home of the run's own")
        var none = safe
        none["MAILMIND_HOME"] = nil
        XCTAssertNotNil(refusal(none))
    }

    func testItRefusesARunWithoutTheKeychainsStandInAKeyOrAPausedClock() {
        for (name, value) in [("MAILMIND_TEST_KEYCHAIN", ""), ("MAILMIND_DB_KEY", ""), ("MAILMIND_TEST_CLOCK", "running")] {
            var run = safe
            run[name] = value
            XCTAssertNotNil(refusal(run), name)
        }
    }

    func testTmpAndPrivateTmpAreOneFolder() {
        XCTAssertTrue(DriverSafety.within("/tmp/mm-home-x", "/private/tmp"))
        XCTAssertTrue(DriverSafety.within("/private/var/folders/a/T/x", "/var/folders/a/T"))
        XCTAssertFalse(DriverSafety.within("/tmp", "/tmp"))
        XCTAssertFalse(DriverSafety.within("/Users/someone", "/"))
    }
}
