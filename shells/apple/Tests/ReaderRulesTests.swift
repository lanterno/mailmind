import Foundation
import XCTest

/// A link's risk, as the page's `linkRisk.test.ts` holds it.
final class LinkRiskTests: XCTestCase {
    func testAPlainLinkAndAnythingThatIsntHTTPGoThrough() {
        XCTAssertNil(LinkRisk.risk(href: "https://guild.example.test/events", text: "our events", senderDomain: "guild.example.test"))
        XCTAssertNil(LinkRisk.risk(href: "mailto:bo@example.test", text: "write to Bo", senderDomain: nil))
        XCTAssertNil(LinkRisk.risk(href: "not a url", text: "x", senderDomain: nil))
    }

    func testPunycodeIsNamed() {
        XCTAssertTrue(LinkRisk.risk(href: "https://xn--pypal-4ve.example.test/", text: "pay", senderDomain: nil)?
            .contains("punycode") ?? false)
    }

    func testTextNamingAnotherDomain() throws {
        let said = try XCTUnwrap(LinkRisk.risk(href: "https://evil.example.test/", text: "bank.example.test", senderDomain: nil))
        XCTAssertTrue(said.contains("different domain (evil.example.test)"), said)
        XCTAssertTrue(said.contains("bank.example.test"), said)
        for href in ["https://evil.example.test/bank.example.org", "https://evil.example.test/?u=bank.example.org",
                     "https://bank.example.org.evil.test/"]
        {
            XCTAssertTrue(LinkRisk.risk(href: href, text: "bank.example.org", senderDomain: nil)?
                .contains("different domain") ?? false, "a name planted in a path, a query or a subdomain: \(href)")
        }
        XCTAssertNil(LinkRisk.risk(href: "https://tracker.example.test/r?to=https%3A%2F%2Fshop.example.org%2Fa",
                                   text: "shop.example.org", senderDomain: nil), "a redirector that really goes there")
        XCTAssertNil(LinkRisk.risk(href: "https://mail.shop.example.org/a", text: "shop.example.org", senderDomain: nil))
    }

    func testASignInLinkThatLeavesTheSendersDomain() {
        XCTAssertTrue(LinkRisk.risk(href: "https://other.example.org/x", text: "Verify your account",
                                    senderDomain: "bank.example.test")?.contains("unfamiliar domain") ?? false)
        XCTAssertNil(LinkRisk.risk(href: "https://www.bank.example.test/x", text: "Verify your account", senderDomain: "bank.example.test"))
        XCTAssertNil(LinkRisk.risk(href: "https://login.bank.example.test/x", text: "Verify your account",
                                   senderDomain: "mail.bank.example.test"))
        XCTAssertNil(LinkRisk.risk(href: "https://other.example.org/x", text: "Read more", senderDomain: "bank.example.test"))
        XCTAssertTrue(LinkRisk.risk(href: "https://login.evil.co.uk/", text: "Sign in", senderDomain: "bank.co.uk")?
            .contains("unfamiliar domain") ?? false)
        XCTAssertNil(LinkRisk.risk(href: "https://login.bank.co.uk/", text: "Sign in", senderDomain: "bank.co.uk"))
    }

    func testTheHostsOfALinkAndOfAnyURLInsideIt() {
        XCTAssertEqual(Set(LinkRisk.linkHosts("https://www.a.example.test/r?u=https%3A%2F%2Fb.example.test")),
                       ["a.example.test", "b.example.test"])
        XCTAssertEqual(Set(LinkRisk.linkHosts("https://a.example.test/%zz")), ["a.example.test"])
    }

    /// The domain an organisation registers, under a suffix of one label, of two under a country's own second level
    /// (co.uk, com.au) or a few others like it, and under example.test, where each fictional organisation has a name.
    func testRegistrableDomains() {
        let cases = [("example.test", "example.test"), ("mail.example.test", "mail.example.test"),
                     ("a.b.example.test", "b.example.test"), ("www.pellbank.example.test", "pellbank.example.test"),
                     ("bank.co.uk", "bank.co.uk"), ("mail.bank.co.uk", "bank.co.uk"), ("shop.com.au", "shop.com.au"),
                     ("www.shop.com.au", "shop.com.au"), ("station.police.uk", "station.police.uk"),
                     ("www.health.govt.nz", "health.govt.nz"), ("t.co", "t.co"), ("x.t.co", "t.co"), ("co.uk", "co.uk"),
                     ("MAIL.Example.Test", "mail.example.test"), ("guild.test", "guild.test"), ("a.guild.test", "guild.test")]
        for (host, domain) in cases { XCTAssertEqual(LinkRisk.registrable(host), domain, host) }
    }

    /// The name a link's words give is read whole, as a person reads it, less its www.: a link that goes there passes,
    /// however many labels the name and its suffix have; one that goes elsewhere is said with the name in full.
    func testTheNameInTheLinksWordsIsReadWhole() throws {
        let fine = [("https://www.pellbank.example.test/statement", "www.pellbank.example.test"),
                    ("https://pellbank.example.test/", "Sign in at www.pellbank.example.test to see it."),
                    ("https://pellbank.example.test/", "accounts.pellbank.example.test"),
                    ("https://www.bank.co.uk/", "www.bank.co.uk"), ("https://login.bank.co.uk/", "www.bank.co.uk"),
                    ("https://shop.com.au/cart", "Visit www.shop.com.au today")]
        for (href, text) in fine {
            XCTAssertNil(LinkRisk.risk(href: href, text: text, senderDomain: nil), "\(text) goes to \(href)")
        }
        let said = try XCTUnwrap(LinkRisk.risk(href: "https://pellbank-secure.example.test/login",
                                               text: "www.pellbank.example.test", senderDomain: nil))
        XCTAssertTrue(said.contains("than the link text suggests (pellbank.example.test)"), said)
        for href in ["https://evil.co.uk/", "https://bank.co.uk.evil.example.test/", "https://www.bank.co/"] {
            XCTAssertTrue(LinkRisk.risk(href: href, text: "www.bank.co.uk", senderDomain: nil)?
                .contains("different domain") ?? false, href)
        }
    }

    /// Each name under example.test is an organisation of its own, as each under co.uk is: a sign-in link to another
    /// is unfamiliar, and a Reply-To at another leaves the sender's.
    func testANameUnderExampleTestIsAnOrganisationOfItsOwn() {
        XCTAssertTrue(LinkRisk.risk(href: "https://pellbank-secure.example.test/login", text: "Sign in to your account",
                                    senderDomain: "pellbank.example.test")?.contains("unfamiliar domain") ?? false)
        XCTAssertNil(LinkRisk.risk(href: "https://login.pellbank.example.test/", text: "Sign in",
                                   senderDomain: "notices.pellbank.example.test"))
        XCTAssertTrue(LinkRisk.leavesOrganisation("desk@elsewhere.example.test", "bo@bank.example.test"))
        XCTAssertFalse(LinkRisk.leavesOrganisation("desk@help.bank.example.test", "bo@bank.example.test"))
    }

    func testLeavingTheOrganisation() {
        XCTAssertFalse(LinkRisk.leavesOrganisation("desk@bank.test", "ceo@bank.test"))
        XCTAssertFalse(LinkRisk.leavesOrganisation("desk@support.bank.test", "ceo@bank.test"))
        XCTAssertFalse(LinkRisk.leavesOrganisation("desk@support.bank.test", "ceo@mail.bank.test"))
        XCTAssertFalse(LinkRisk.leavesOrganisation("DESK@BANK.TEST", "ceo@bank.test"))
        XCTAssertTrue(LinkRisk.leavesOrganisation("desk@other.test", "ceo@bank.test"))
        XCTAssertTrue(LinkRisk.leavesOrganisation("desk@bank.test.other.test", "ceo@bank.test"))
        XCTAssertTrue(LinkRisk.leavesOrganisation("desk@evil.co.uk", "ceo@bank.co.uk"))
        XCTAssertFalse(LinkRisk.leavesOrganisation("desk@mail.bank.co.uk", "ceo@bank.co.uk"))
        XCTAssertFalse(LinkRisk.leavesOrganisation("", "ceo@bank.test"))
        XCTAssertFalse(LinkRisk.leavesOrganisation("desk@other.test", ""))
    }
}

/// A mail's look and its plain quote, as the page's `mailDoc.test.ts` holds them.
final class MailLookTests: XCTestCase {
    private let night = MailLook.Palette(ink: "#fff", background: "#111", link: "#9cf", quoteLine: "#555", quoteInk: "#aaa")

    private func look(html: Bool, colors: API.MailColors = .auto, font: API.ReadFont = .sans, size: API.ReadSize = .m,
                      dark: Bool = false) -> MailLook
    {
        MailLook.of(html: html, prefs: .init(colors: colors, font: font, size: size), darkAppearance: dark, dark: night)
    }

    func testRichMailIsDarkenedByInvertingItAndMediaBack() {
        let dark = look(html: true, colors: .dark)
        XCTAssertTrue(dark.inverted)
        XCTAssertTrue(dark.css.contains("body img,body picture,body video"))
        XCTAssertTrue(dark.css.contains("html{background:#111}"))
    }

    func testRichMailKeepsItsOwnColorsOnAutoInEitherAppearance() {
        XCTAssertFalse(look(html: true).inverted)
        XCTAssertFalse(look(html: true, dark: true).inverted)
        XCTAssertFalse(look(html: true, colors: .original, dark: true).inverted)
    }

    func testPlainTextTakesTheAppsColorsAndIsNeverInverted() {
        let atNight = look(html: false, dark: true)
        XCTAssertFalse(atNight.inverted)
        XCTAssertTrue(atNight.css.contains("html{background:transparent}body{margin:0px;"), "on the pane's paper")
        XCTAssertTrue(atNight.css.contains("color:#fff;"))
        XCTAssertFalse(atNight.sheet)
        let byDay = look(html: false)
        XCTAssertTrue(byDay.css.contains("html{background:transparent}"))
        XCTAssertTrue(byDay.css.contains("color:#24221E;"))
        let darkInDaylight = look(html: false, colors: .dark)
        XCTAssertTrue(darkInDaylight.sheet, "darker than the light: a sheet of its own")
        XCTAssertTrue(darkInDaylight.css.contains("html{background:#111}body{margin:14px;"))
        XCTAssertTrue(darkInDaylight.css.contains("color:#fff;"))
        XCTAssertTrue(darkInDaylight.css.contains("a{color:#9cf}"))
        XCTAssertTrue(darkInDaylight.css.contains("border-left:2px solid #555"))
        let lightAtNight = look(html: false, colors: .original, dark: true)
        XCTAssertTrue(lightAtNight.sheet, "lighter than the night: a sheet of its own")
        XCTAssertTrue(lightAtNight.css.contains("html{background:#FFFDF8}"))
        XCTAssertTrue(lightAtNight.css.contains("color:#24221E;"))
    }

    func testPlainTextIsSetByTheReadingSettingsAndRichMailKeepsItsType() {
        XCTAssertTrue(look(html: false, font: .mono, size: .xl).css.contains("font:21px/1.55 ui-monospace"))
        XCTAssertTrue(look(html: false, size: .m).css.contains("font:17px/1.72 \"MailMind Read\""))
        XCTAssertTrue(look(html: false, font: .serif, size: .s).css.contains("font:15px/1.72 \"Iowan Old Style\""))
        let rich = look(html: true, font: .mono, size: .xl)
        XCTAssertFalse(rich.css.contains("monospace"))
        XCTAssertTrue(rich.css.contains("body{margin:0px;font:17px/1.72 \"MailMind Read\""),
                      "where it sets no type of its own, the reading face at the size with nothing said")
        XCTAssertFalse(rich.sheet)
    }

    func testRichMailInItsOwnColoursAtNightIsAWhiteSheet() {
        let rich = look(html: true, colors: .original, dark: true)
        XCTAssertTrue(rich.sheet)
        XCTAssertFalse(rich.inverted)
        XCTAssertTrue(rich.css.contains("html{background:#ffffff}"))
    }

    func testTheReadingFaceGoesInOnlyWhereTheTextIsSetInIt() {
        let document = "<html><head></head><body></body></html>"
        XCTAssertTrue(look(html: false).readingFace)
        XCTAssertTrue(look(html: true, font: .serif).readingFace)
        XCTAssertFalse(look(html: false, font: .serif).readingFace)
        XCTAssertFalse(look(html: false, font: .mono).readingFace)
        XCTAssertFalse(look(html: false, font: .mono).dress(document).contains("@font-face"))
        let face = FrameFont.face
        XCTAssertFalse(face.isEmpty, "the app carries Nunito Sans")
        XCTAssertTrue(look(html: false).dress(document).contains("<style>" + face))
    }

    func testAFrameThatCantBeClearPaintsThePanesPaperItself() {
        let document = "<html><head></head><body><pre>Hi</pre></body></html>"
        let dusk = MailLook.Palette(ink: "#eee", background: "#223", link: "#9cf", quoteLine: "#555", quoteInk: "#aaa")
        let plain = MailLook.of(html: false, prefs: .init(), darkAppearance: true, dark: night, light: dusk)
        XCTAssertFalse(plain.sheet)
        XCTAssertEqual(plain.paper, "#223", "the light's own paper, whichever palette the mail's ink comes from")
        XCTAssertFalse(plain.dress(document).contains("html{background:#223}"), "a clear frame shows the paper")
        XCTAssertTrue(plain.dress(document, clear: false).contains("html{background:#223}</style>"),
                      "one that can't paints it, after the look's transparent so it wins")
        let darkened = MailLook.of(html: true, prefs: .init(colors: .dark), darkAppearance: true, dark: night,
                                   light: dusk)
        XCTAssertTrue(darkened.dress(document, clear: false).contains("html{background:#223}</style>"))
        XCTAssertTrue(look(html: false).dress(document, clear: false).contains("html{background:#FFFDF8}</style>"))
        let sheet = look(html: true, colors: .original, dark: true)
        XCTAssertNil(sheet.paper)
        XCTAssertEqual(sheet.dress(document, clear: false), sheet.dress(document), "a sheet paints its own")
    }

    func testTheFoldIsDrawnInEveryLook() {
        for each in [look(html: true), look(html: true, colors: .dark), look(html: false)] {
            XCTAssertTrue(each.css.contains("details.mm-quote"))
        }
    }

    func testTheLookGoesInTheDocumentsHeadAndNowhereElse() {
        let document = "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"></head><body><p>&lt;/head&gt;</p></body></html>"
        let dressed = look(html: true).dress(document)
        XCTAssertTrue(dressed.hasPrefix("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><style>"))
        XCTAssertTrue(dressed.hasSuffix("</style></head><body><p>&lt;/head&gt;</p></body></html>"))
        XCTAssertEqual(look(html: true).dress("no head"), "no head")
    }

    func testAPlainQuoteIsFound() {
        XCTAssertEqual(PlainQuote.fold("Hi\nOn Mon Bo wrote:\nold").map { [$0.head, $0.quoted] }, ["Hi", "On Mon Bo wrote:\nold"])
        XCTAssertEqual(PlainQuote.fold("Hi\n> old\n> older\n").map { [$0.head, $0.quoted] }, ["Hi", "> old\n> older\n"])
        XCTAssertNil(PlainQuote.fold("On Mon Bo wrote:\nold"), "nothing above it")
        XCTAssertNil(PlainQuote.fold("\n> old"))
        XCTAssertNil(PlainQuote.fold("just text"))
    }

    func testAPlainDocumentsQuoteFoldsIntoDetailsAndEverythingStaysEscaped() {
        let text = "Hi <Bo> & \"Cy\"\nOn Mon Bo wrote:\n> it's <b>old</b>"
        let document = "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><base target=\"_blank\"></head><body><pre>"
            + PlainQuote.escaped(text) + "</pre></body></html>"
        let folded = PlainQuote.foldDocument(document)
        XCTAssertTrue(folded.contains("<pre>Hi &lt;Bo&gt; &amp; &quot;Cy&quot;</pre>"
            + "<details class=\"mm-quote\"><summary><span class=\"mm-show\">··· Show quoted text</span>"
            + "<span class=\"mm-hide\">··· Hide quoted text</span></summary>"))
        XCTAssertTrue(folded.hasSuffix("<pre>On Mon Bo wrote:\n&gt; it&#x27;s &lt;b&gt;old&lt;/b&gt;</pre></body></html>"))
        XCTAssertFalse(folded.contains("<b>"), "the mail's text never becomes markup")
        XCTAssertEqual(PlainQuote.unescaped(PlainQuote.escaped("a &amp; <b> \"q\" 'x'")), "a &amp; <b> \"q\" 'x'")
        let unquoted = document.replacingOccurrences(of: "On Mon Bo wrote:\n&gt;", with: "plain")
        XCTAssertEqual(PlainQuote.foldDocument(unquoted), unquoted, "no quote, no change")
        let rich = "<html><head></head><body><p>On Mon Bo wrote:</p></body></html>"
        XCTAssertEqual(PlainQuote.foldDocument(rich), rich, "only the core's plain document is folded")
    }

    func testTheReadingMeasureAndAFilesSize() {
        XCTAssertEqual([API.ReadWidth.narrow, .medium, .wide].map { MailLook.measure($0, size: .m) }, [561, 680, 850])
        XCTAssertEqual([0, 512, 2048, 3_500_000].map(FileSize.words), ["", "512 B", "2 KB", "3.3 MB"])
    }
}
