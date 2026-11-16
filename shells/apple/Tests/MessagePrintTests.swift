import WebKit
import XCTest

/// File › Print: the open message on paper, its head over its body, in the Day light, with the quote open and nothing
/// of the mail's words read as markup.
@MainActor
final class MessagePrintTests: XCTestCase {
    private let document = "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"></head>"
        + "<body><pre>Hi\nOn Mon Bo wrote:\nold</pre></body></html>"

    func testTheHeadSaysWhoWhenAndWhatWithTheMailsWordsEscaped() {
        let head = MessagePrinter.head(opened(from: "bo@example.test", to: "ada@example.test",
                                              subject: "Tickets <b>& more</b>"))
        XCTAssertTrue(head.contains("Tickets &lt;b&gt;&amp; more&lt;/b&gt;"), head)
        XCTAssertTrue(head.contains("<b>From:</b> Bo &lt;bo@example.test&gt;"), head)
        XCTAssertTrue(head.contains("<b>To:</b> ada@example.test"), head)
        XCTAssertTrue(head.contains("<b>Date:</b> Mon, 2 Nov 2026 12:00"), head)
        XCTAssertFalse(head.contains("Cc:"), "an empty Cc says nothing")
        XCTAssertFalse(head.contains("<b>Tickets"), "a subject's markup stays words")
        XCTAssertTrue(MessagePrinter.head(opened(cc: "grace@example.test")).contains("<b>Cc:</b> grace@example.test"))
        XCTAssertTrue(MessagePrinter.head(opened(subject: "")).contains("(no subject)"))
    }

    func testThePrintedDocumentStartsWithItsHeadAndKeepsTheQuoteOpen() {
        let look = MessagePrinter.look(html: false, prefs: ReadingPrefs())
        let page = CoreAnswer(status: 200, contentType: "text/html; charset=utf-8", headers: [], body: Data(document.utf8))
        let printed = String(decoding: FrameGuard.dressed(page, look: look, html: false, head: "<div>HEAD</div>"),
                             as: UTF8.self)
        XCTAssertTrue(printed.contains("<body><div>HEAD</div><pre>"), printed)
        XCTAssertTrue(printed.contains("<style>"), "the look is in the head")
        XCTAssertFalse(printed.contains("<details"), "paper can't unfold a quote")
        let shown = String(decoding: FrameGuard.dressed(page, look: look, html: false), as: UTF8.self)
        XCTAssertTrue(shown.contains("<details"), "the frame still folds it")
        XCTAssertFalse(shown.contains("HEAD"))
    }

    func testAHeadGoesAfterTheBodysOpeningTagOrElseTheHead() {
        XCTAssertEqual(MessagePrinter.headed("<head></head><BODY class=\"m\">text", head: "H"),
                       "<head></head><BODY class=\"m\">Htext")
        XCTAssertEqual(MessagePrinter.headed("<head></head>text", head: "H"), "<head></head>Htext")
        XCTAssertEqual(MessagePrinter.headed("text", head: "H"), "Htext")
    }

    /// Paper is the Day light's, whatever the window's light and the mail colours chosen.
    func testPaperIsNeverDarkened() {
        var prefs = ReadingPrefs()
        prefs.look.colors = .dark
        for html in [false, true] {
            let look = MessagePrinter.look(html: html, prefs: prefs)
            XCTAssertFalse(look.inverted)
            XCTAssertTrue(look.css.contains(MailLook.light.ink), "the Day light's ink")
        }
    }

    /// A message whose images a person asked to see prints with them, as a ticket's code must; another doesn't.
    func testAMessageWithImagesShownPrintsThem() throws {
        let shown = ShownImages()
        let ticket = MailCopy(key: 7, folder: "INBOX", uid: 7, account: Fixture.ada)
        let other = MailCopy(key: 8, folder: "INBOX", uid: 8, account: Fixture.ada)
        XCTAssertTrue(try XCTUnwrap(MessagePrinter.documentURL(ticket, shown: shown)).absoluteString.contains("images=0"))
        shown.show(ticket)
        XCTAssertTrue(try XCTUnwrap(MessagePrinter.documentURL(ticket, shown: shown)).absoluteString.contains("images=1"))
        XCTAssertTrue(try XCTUnwrap(MessagePrinter.documentURL(other, shown: shown)).absoluteString.contains("images=0"))
    }

    func testTheJobIsNamedForItsSubject() {
        XCTAssertEqual(MessagePrinter.jobTitle("Your tickets"), "Your tickets")
        XCTAssertEqual(MessagePrinter.jobTitle("  "), "Message")
        XCTAssertEqual(MessagePrinter.jobTitle(nil), "Message")
    }
}
