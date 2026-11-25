import Foundation
import XCTest

/// The generated types read what the routes answer, and a union reads as the member whose keys its answer fits.
final class APITypesTests: XCTestCase {
    func testAnAnswerReadsAsItsRoutesType() throws {
        let json = #"{"accounts": [], "generated": "2026-11-02T12:00:00Z", "ok": true, "signature_default": "Ada"}"#
        let listed = try JSONDecoder().decode(API.Get.Accounts.Answer.self, from: Data(json.utf8))
        XCTAssertTrue(listed.ok)
        XCTAssertEqual(listed.accounts, [])
        XCTAssertEqual(listed.signatureDefault, "Ada", "a snake_case key reads into its Swift name")
    }

    func testAUnionReadsAsTheMemberItsKeysFit() throws {
        let read = try JSONDecoder().decode(API.MessageBodyAnswer.self, from: Data(#"{"ok": true, "prefetched": true}"#.utf8))
        XCTAssertEqual(read, .prefetched(API.Prefetched(ok: true, prefetched: true)))
        let again = try JSONDecoder().decode(API.MessageBodyAnswer.self, from: JSONEncoder().encode(read))
        XCTAssertEqual(again, read, "a union writes what it read")
    }

    func testAnAnswerNoMemberFitsIsAnError() {
        XCTAssertThrowsError(try JSONDecoder().decode(API.MessageBodyAnswer.self, from: Data(#"{"nothing": 1}"#.utf8)))
    }

    func testAnyJSONReadsAndWritesAsItIs() throws {
        let json = #"{"a": [1, 2.5, "three", true, null], "b": {"c": false}}"#
        let value = try JSONDecoder().decode(JSONValue.self, from: Data(json.utf8))
        XCTAssertEqual(value, .object(["a": .array([.number(1), .number(2.5), .string("three"), .bool(true), .null]),
                                       "b": .object(["c": .bool(false)])]))
        XCTAssertEqual(try JSONDecoder().decode(JSONValue.self, from: JSONEncoder().encode(value)), value)
    }
}

/// A route's refusal is its problem document, as the page reads it, and a query is written as the page writes one.
final class ProblemTests: XCTestCase {
    private func answer(_ status: UInt16, _ body: String) -> CoreAnswer {
        CoreAnswer(status: status, contentType: "application/problem+json", headers: [], body: Data(body.utf8))
    }

    func testARefusalIsItsProblemDocument() {
        let problem = Problem(answer(400, #"""
            {"type": "/problems/refused", "title": "Refused", "status": 400, "detail": "Choose a later time.", "busy": true}
            """#))
        XCTAssertEqual(problem.status, 400)
        XCTAssertEqual(problem.type, "/problems/refused")
        XCTAssertEqual(problem.title, "Refused")
        XCTAssertEqual(problem.more, ["busy": .bool(true)], "what the page acts on beyond the standard fields is kept")
        XCTAssertTrue(problem.isRefusal)
        XCTAssertEqual(problem.description, "Choose a later time.", "a person is told the server's words")
    }

    func testAnAnswerWithNoDocumentSaysItsStatus() {
        let problem = Problem(answer(503, ""))
        XCTAssertEqual(problem.type, "about:blank")
        XCTAssertFalse(problem.title.isEmpty)
        XCTAssertNil(problem.detail)
        XCTAssertFalse(problem.isRefusal, "a 503 may answer otherwise later")
    }

    func testAnAnswerThatIsntItsTypeIsUnreadableNotAProblem() {
        XCTAssertThrowsError(try Core.decoded(API.Listed.self, from: answer(200, "{}"))) { error in
            XCTAssertEqual((error as? Unreadable)?.status, 200)
        }
        XCTAssertThrowsError(try Core.decoded(API.Listed.self, from: answer(404, #"{"type": "/problems/not-found"}"#))) {
            XCTAssertEqual(($0 as? Problem)?.type, "/problems/not-found")
        }
    }

    func testAQueryIsEncodedAsThePageEncodesIt() {
        XCTAssertEqual(Core.query(["account": "ada+news@example.test", "folder": "Archive/2026 Q1", "uid": nil]),
                       "?account=ada%2Bnews%40example.test&folder=Archive%2F2026%20Q1")
        XCTAssertEqual(Core.query([:]), "")
    }
}

/// A run that isn't a person's opens only the home it names, never MailMind's own folder or the login Keychain.
final class CoreHomeTests: XCTestCase {
    func testANamedHomeIsOpenedWithItsStandIn() throws {
        let home = try CoreHome.ofThisProcess([
            "MAILMIND_HOME": "/private/tmp/home", "MAILMIND_TEST_KEYCHAIN": "/private/tmp/home/keychain.json",
            "MAILMIND_DB_KEY": "fixture", "NOT_MAILMINDS": "left out",
        ], readOnly: false)
        XCTAssertEqual(home.folder, "/private/tmp/home")
        XCTAssertEqual(home.standIn, "/private/tmp/home/keychain.json")
        XCTAssertEqual(home.variables, ["MAILMIND_DB_KEY": "fixture"], "only what MailMind reads reaches the core")
    }

    func testAMeasureDemoOrTestRunWithNoHomeOpensNothing() {
        for variable in CoreHome.notAPersonsRun {
            XCTAssertThrowsError(try CoreHome.ofThisProcess([variable: "1", "MAILMIND_HOME": ""], readOnly: false)) {
                XCTAssertEqual($0 as? NoHomeNamed, NoHomeNamed(variable: variable))
            }
        }
    }

    /// MailMind Dev, the Debug build, is never a person's MailMind: with no home named it opens nothing, and with one
    /// it opens that.
    func testMailMindDevOpensOnlyAHomeItIsGiven() throws {
        XCTAssertThrowsError(try CoreHome.ofThisProcess(["HOME": "/Users/ada"], readOnly: false, dev: true)) {
            XCTAssertEqual($0 as? DevNamesNoHome, DevNamesNoHome())
        }
        let home = try CoreHome.ofThisProcess(["MAILMIND_HOME": "/private/tmp/home"], readOnly: false, dev: true)
        XCTAssertEqual(home.folder, "/private/tmp/home")
        XCTAssertFalse(CoreHome.isAPersonsRun(["HOME": "/Users/ada"], dev: true))
        XCTAssertTrue(CoreHome.isAPersonsRun(["HOME": "/Users/ada"], dev: false))
    }

    /// Settings' read-only switch reaches the core as the profile a read-only run names, unless the environment names
    /// one of its own.
    func testTheReadOnlySwitchRunsTheCoreReadOnly() throws {
        XCTAssertEqual(CoreHome.variables(["HOME": "/Users/ada"], readOnly: true),
                       ["HOME": "/Users/ada", "MAILMIND_PROFILE": "read-only"])
        XCTAssertEqual(CoreHome.variables(["HOME": "/Users/ada"], readOnly: false), ["HOME": "/Users/ada"])
        XCTAssertEqual(CoreHome.variables(["MAILMIND_PROFILE": "dev"], readOnly: true), ["MAILMIND_PROFILE": "dev"])
    }

    /// The switch is the person's own MailMind's: a demo or test run keeps preferences of its own, and with the switch
    /// on still runs only as it was started.
    func testTheReadOnlySwitchNeverReachesADemoOrTestRun() throws {
        XCTAssertEqual(try CoreHome.ofThisProcess(["MAILMIND_HOME": "/private/tmp/home"], readOnly: true).variables, [:])
        let started = try CoreHome.ofThisProcess(["MAILMIND_HOME": "/private/tmp/home", "MAILMIND_PROFILE": "read-only"],
                                                 readOnly: false)
        XCTAssertEqual(started.variables, ["MAILMIND_PROFILE": "read-only"], "a run started read only stays so")
        for variable in CoreHome.notAPersonsRun {
            XCTAssertNil(CoreHome.variables([variable: "1"], readOnly: true)["MAILMIND_PROFILE"], variable)
        }
    }

    /// Only a person's run opens itself again: another names a home of its own, or is a measure, demo or test run.
    func testOnlyAPersonsRunIsOne() {
        XCTAssertTrue(CoreHome.isAPersonsRun(["HOME": "/Users/ada"]))
        XCTAssertTrue(CoreHome.isAPersonsRun(["MAILMIND_HOME": ""]))
        XCTAssertFalse(CoreHome.isAPersonsRun(["MAILMIND_HOME": "/private/tmp/home"]))
        for variable in CoreHome.notAPersonsRun {
            XCTAssertFalse(CoreHome.isAPersonsRun([variable: "1"]), variable)
        }
    }
}

/// The core in this process, on a temp home of its own: no account, a fixture key, a file in the Keychain's place.
final class CoreTests: XCTestCase {
    /// Guards only this test's empty database.
    private static let key = "8f3a1c5e7b9d2f4a6c8e0b1d3f5a7c9e2b4d6f8a0c1e3b5d7f9a2c4e6b8d0f1a"
    private var folder: URL!

    override func setUpWithError() throws {
        folder = FileManager.default.temporaryDirectory.appending(path: "mailmind-core-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: folder)
    }

    private func opened() async throws -> Core {
        try await Core.open(CoreHome(folder: folder.path(percentEncoded: false), variables: ["MAILMIND_DB_KEY": Self.key],
                                     standIn: folder.appending(path: "keychain.json").path(percentEncoded: false)))
    }

    func testARouteAnswersAsItsType() async throws {
        let core = try await opened()
        let listed = try await core.get(API.Get.Accounts.self)
        XCTAssertTrue(listed.ok)
        XCTAssertEqual(listed.accounts, [])
        await core.close()
    }

    func testARefusalIsThrownAsItsProblem() async throws {
        let core = try await opened()
        do {
            _ = try await core.get(API.Get.MessageBody.self, ["key": "4242", "folder": "INBOX"])
            XCTFail("a message that isn't there opened")
        } catch let problem as Problem {
            XCTAssertEqual(problem.status, 404)
            XCTAssertEqual(problem.type, "/problems/not-found")
        }
        await core.close()
    }

    func testTheEventStreamBeginsWithHelloAndEndsWhenTheCoreCloses() async throws {
        let core = try await opened()
        var events = try await core.events().makeAsyncIterator()
        let first = await events.next()
        XCTAssertEqual(first?.name, "hello")
        await core.close()
        var after = 0
        while await events.next() != nil {
            after += 1
            XCTAssertLessThan(after, 100, "the stream ended when the core closed")
        }
    }

    func testAClosedCoreAnswersEveryRequest503() async throws {
        let core = try await opened()
        await core.close()
        let answer = await core.send("GET", "/api/health", Data())
        XCTAssertEqual(answer.status, 503)
        XCTAssertEqual(answer.contentType, "application/problem+json")
    }
}
