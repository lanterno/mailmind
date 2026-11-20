import Foundation
import XCTest

/// What the app reads from a frame, what a link does, and what stands around a body.
@MainActor
final class ReaderTests: XCTestCase {
    func testTheFramesMessagesAreReadAndNothingElse() {
        XCTAssertEqual(FrameMessage(["kind": "link", "href": "https://a.example.test/", "text": "A"]),
                       .link(FrameLink(href: "https://a.example.test/", text: "A")))
        XCTAssertEqual(FrameMessage(["kind": "height", "value": NSNumber(value: 812.5)]), .height(812.5))
        XCTAssertNil(FrameMessage(["kind": "height", "value": NSNumber(value: Double.nan)]))
        XCTAssertNil(FrameMessage(["kind": "height", "value": NSNumber(value: -1)]))
        XCTAssertNil(FrameMessage(["kind": "navigate", "href": "https://a.example.test/"]))
        XCTAssertNil(FrameMessage("link"))
        XCTAssertEqual(FrameMessage(["kind": "height", "value": NSNumber(value: 1e12)]), .height(1_000_000))
        if case .link(let link)? = FrameMessage(["kind": "link", "href": String(repeating: "h", count: 20000),
                                                 "text": String(repeating: "t", count: 9000)])
        {
            XCTAssertEqual(link.href.count, 8192)
            XCTAssertEqual(link.text.count, 2000)
        } else {
            XCTFail("a long link is still a link, cut short")
        }
    }

    /// The link under the pointer comes as its href alone, and only a web or mail address MailMind would open is said:
    /// none for an empty href, a script's, a file's or a fragment, and nothing at all for a message with more in it.
    func testTheLinkUnderThePointerIsReadNarrowly() {
        XCTAssertEqual(FrameMessage(["kind": "hover", "href": "https://a.example.test/x?y=1"]),
                       .hover(URL(string: "https://a.example.test/x?y=1")))
        XCTAssertEqual(FrameMessage(["kind": "hover", "href": "mailto:bo@example.test?subject=Hi"]),
                       .hover(URL(string: "mailto:bo@example.test?subject=Hi")))
        XCTAssertEqual(FrameMessage(["kind": "hover", "href": ""]), .hover(nil), "off the link")
        for unsaid in ["javascript:alert(1)", "file:///etc/hosts", "#top", "data:text/html,x", "https://",
                       "https://a.example.test/\u{0}x", String(repeating: "h", count: 9000)] {
            XCTAssertEqual(FrameMessage(["kind": "hover", "href": unsaid]), .hover(nil), unsaid)
        }
        XCTAssertNil(FrameMessage(["kind": "hover", "href": "https://a.example.test/", "text": "A"]), "only the href")
        XCTAssertNil(FrameMessage(["kind": "hover", "href": NSNumber(value: 1)]), "a string")
        XCTAssertNil(FrameMessage(["kind": "hover"]))

        XCTAssertEqual(LinkHover.words(URL(string: "https://a.example.test/x")!), "https://a.example.test/x")
        XCTAssertEqual(LinkHover.words(URL(string: "mailto:bo%40example.test?subject=Hi")!), "bo@example.test",
                       "a mail address as the address")
    }

    func testALinkOpensTheComposerTheBrowserOrNothing() {
        XCTAssertEqual(LinkOpening.of(href: "mailto:Bo%20B%20<bo@example.test>?subject=Lunch%20plans&body=x", text: "", senderDomain: nil),
                       .compose(to: ["bo@example.test"], subject: "Lunch plans"))
        XCTAssertEqual(LinkOpening.of(href: "https://a.example.test/x", text: "a.example.test", senderDomain: nil),
                       .browse(URL(string: "https://a.example.test/x")!, warning: nil))
        if case .browse(_, let warning?) = LinkOpening.of(href: "https://evil.example.test/", text: "bank.example.test",
                                                          senderDomain: nil)
        {
            XCTAssertTrue(warning.contains("different domain"))
        } else {
            XCTFail("a risky link is opened only after a warning")
        }
        for refused in ["javascript:alert(1)", "file:///etc/hosts", "data:text/html,hi", "mailmind-frame://x", "https://", "ftp://a.example.test/"] {
            XCTAssertEqual(LinkOpening.of(href: refused, text: "", senderDomain: nil), .refuse, refused)
        }
    }

    func testTheDocumentIsDressedOnlyWhenItIsOne() {
        let look = MailLook.of(html: false, prefs: .init(), darkAppearance: false, dark: MailLook.light)
        let document = "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><base target=\"_blank\"></head><body><pre>Hi\nOn Mon Bo wrote:\nold</pre></body></html>"
        let page = CoreAnswer(status: 200, contentType: "text/html; charset=utf-8", headers: [], body: Data(document.utf8))
        let dressed = String(decoding: FrameGuard.dressed(page, look: look, html: false), as: UTF8.self)
        XCTAssertTrue(dressed.contains("<style>"))
        XCTAssertTrue(dressed.contains("<details class=\"mm-quote\">"))
        let richly = String(decoding: FrameGuard.dressed(page, look: look, html: true), as: UTF8.self)
        XCTAssertFalse(richly.contains("<details"), "rich mail's quote is the sanitizer's to fold, not ours")
        let refusal = CoreAnswer(status: 404, contentType: "application/problem+json", headers: [], body: Data("{}".utf8))
        XCTAssertEqual(FrameGuard.dressed(refusal, look: look, html: false), Data("{}".utf8))
    }

    func testAReplyToThatLeavesTheOrganisationIsSaid() {
        let mine = SendGuard.mineSet(["ada@example.test"])
        XCTAssertEqual(ReplyGoesElsewhere.text(opened(from: "ceo@bank.test", replyTo: "desk@other.test"), mine: mine),
                       "Replies go to desk@other.test, not to the sender, ceo@bank.test.")
        XCTAssertNil(ReplyGoesElsewhere.text(opened(from: "ceo@bank.test", replyTo: "desk@support.bank.test"), mine: mine))
        XCTAssertNil(ReplyGoesElsewhere.text(opened(from: "ceo@bank.test", replyTo: "ada@example.test"), mine: mine))
        XCTAssertNil(ReplyGoesElsewhere.text(opened(from: "ceo@bank.test"), mine: mine))
    }

    func testAttachmentsShownAndTheirNamesMadeSafe() {
        let part = { (inline: Int, cid: String?) in
            API.AttachmentPart(contentId: cid, contentType: "image/png", filename: "a.png", isInline: inline, partId: "2",
                               size: 1, travels: true)
        }
        XCTAssertEqual(AttachmentsRow.shown([part(1, "x@y"), part(0, "x@y"), part(1, nil)]).count, 2,
                       "a picture the text shows isn't listed again")
        XCTAssertEqual(AttachmentsRow.safeName("../../etc/passwd"), "passwd")
        XCTAssertEqual(AttachmentsRow.safeName("a\u{0}b\"c\nd.pdf"), "a_b_c_d.pdf")
        XCTAssertEqual(AttachmentsRow.safeName(".."), "attachment")
        XCTAssertEqual(AttachmentsRow.safeName("  "), "attachment")
        XCTAssertEqual(AttachmentsRow.safeName("C:\\Users\\bo\\report.pdf"), "report.pdf")
    }

    /// A conversation is asked for by the names its route reads, `account` and `thread_id`. Asked by `id`, the route
    /// answered "missing account/thread", and the pane showed that in place of the conversation.
    func testAConversationIsAskedForByItsAccountAndThreadID() async throws {
        let member = API.ThreadMessage(answered: false, flagged: false, folder: "INBOX", hasAttach: false, isJunk: false,
                                       key: 21, seen: true, sentTo: [], sentToMore: 0,
                                       snippet: "See you then", subject: "Plans", uid: 21, when: "Mon")
        let thread = API.Thread(account: Fixture.work, count: 1, items: [member], ok: true, replyKey: 21, threadId: "t1")
        let routes = FakeRoutes()
        routes.answer(API.Get.Thread.path, String(decoding: try JSONEncoder().encode(thread), as: UTF8.self))
        let conversation = ConversationModel(routes: routes, account: Fixture.work, id: "t1", profile: { nil })
        await conversation.load()
        XCTAssertEqual(routes.calls(to: API.Get.Thread.path).map(\.query), [["account": Fixture.work, "thread_id": "t1"]])
        XCTAssertEqual(conversation.stage, .open(thread))
    }
}

