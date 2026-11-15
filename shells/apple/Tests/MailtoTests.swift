import XCTest

/// A mailto: link's message, whether a link in mail or macOS hands it to the app, and what waits for the window.
@MainActor
final class MailtoTests: XCTestCase {
    func testALinkGivesItsAddressesCcSubjectAndBody() {
        let mail = Mailto.parse("mailto:Bo%20B%20%3Cbo@example.test%3E,cy@example.test?cc=dee@example.test&subject=Lunch+plans%3F&body=Noon%0Aworks")
        XCTAssertEqual(mail, Mailto(to: ["bo@example.test", "cy@example.test"], cc: ["dee@example.test"],
                                    subject: "Lunch plans?", body: "Noon\nworks"))
        XCTAssertEqual(Mailto.parse("MAILTO:?to=ada@example.test")?.to, ["ada@example.test"])
        XCTAssertNil(Mailto.parse("https://example.test/"))
    }

    func testAPlusInAnAddressIsItself() {
        XCTAssertEqual(Mailto.parse("mailto:ada+news@example.test?cc=bo+x@example.test")?.to, ["ada+news@example.test"])
        XCTAssertEqual(Mailto.parse("mailto:ada+news@example.test?cc=bo+x@example.test")?.cc, ["bo+x@example.test"])
    }

    func testALinkTakesNoBlindCopyAndOneSubjectLine() {
        let mail = Mailto.parse("mailto:bo@example.test?bcc=eve@example.test&subject=Hi%0D%0ABcc:%20eve@example.test")
        XCTAssertEqual(mail?.to, ["bo@example.test"])
        XCTAssertEqual(mail?.seed.bcc, [], "a link never adds a hidden recipient")
        XCTAssertEqual(mail?.subject.contains("\n"), false)
        XCTAssertEqual(mail?.subject.contains("\r"), false)
    }

    func testWhatALinkAsksIsKeptToItsLimits() {
        let long = String(repeating: "a", count: 30_000)
        let mail = Mailto.parse("mailto:bo@example.test?subject=\(long)&body=\(long)")
        XCTAssertEqual(mail?.subject.count, Mailto.subjectLimit)
        XCTAssertEqual(mail?.body.count, Mailto.bodyLimit)
    }

    func testALinkBeforeTheWindowCanWriteWaitsForIt() async throws {
        let composing = Composing()
        let routes = FakeRoutes()
        let digest = String(decoding: try JSONEncoder().encode(Fixture.digest()), as: UTF8.self)
        routes.answer("/api/digest", digest)
        var opened: [UUID] = []
        composing.write(Mailto(to: ["bo@example.test"], subject: "Lunch"))
        composing.openWindow = { opened.append($0) }
        composing.use(routes, digest: nil)
        XCTAssertTrue(opened.isEmpty, "who you are isn't read yet")
        await composing.readDigest()
        XCTAssertEqual(opened.count, 1, "the link's composer opens once the window can write")
        XCTAssertEqual(composing.composers[opened[0]]?.subject, "Lunch")
        await composing.readDigest()
        XCTAssertEqual(opened.count, 1, "and only once")
    }

    func testABodyOpensInPlaceOfTheSignatureAndNoneLeavesIt() {
        XCTAssertEqual(Mailto.parse("mailto:bo@example.test?body=Hello")?.seed.body, "Hello")
        XCTAssertNil(Mailto.parse("mailto:bo@example.test")?.seed.body, "no body: the signature is written in")
    }
}
