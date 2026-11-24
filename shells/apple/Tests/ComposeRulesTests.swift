import Foundation
import XCTest

/// The send guard, as the page's `sendGuard.test.ts` holds it: the same word lists (tests/fixtures), and the same
/// lines word for word.
final class SendGuardTests: XCTestCase {
    private func snapshot(mode: API.ComposeMode = .new, subject: String = "Plans", subject0: String = "", body: String = "",
                          sigBlock: String = "", nAttach: Int = 0, fwdAttach: Int = 0, reading: [String] = [],
                          sendAfter: Int = 0, to: [String] = ["bo@example.test"], cc: [String] = [],
                          mine: [String] = ["ada@example.test"], autoCc: [String]? = nil, fresh: [String] = [],
                          near: [SendGuard.NearMiss] = [], redirect: SendGuard.Snapshot.Redirect? = nil,
                          ack: SendGuard.Acks = .init()) -> SendGuard.Snapshot
    {
        SendGuard.Snapshot(mode: mode, subject: subject, subject0: subject0, body: body, sigBlock: sigBlock,
                           nAttach: nAttach, fwdAttach: fwdAttach, reading: reading, sendAfter: sendAfter,
                           now: 1_000_000_000, to: to, cc: cc, mine: mine, autoCc: autoCc, fresh: fresh, near: near,
                           redirect: redirect, ack: ack)
    }

    private func keys(_ snapshot: SendGuard.Snapshot) -> [String] { SendGuard.check(snapshot).map(\.key) }

    private func people(_ n: Int) -> [String] { (0..<n).map { "p\($0)@example.test" } }

    private struct Words: Decodable {
        let strong: [String]
        let weak: [String]
        let never: [String]
        let replyNever: [String]

        enum CodingKeys: String, CodingKey {
            case strong, weak, never
            case replyNever = "reply_never"
        }
    }

    /// The word lists the page's and the server's tests share.
    private func words() throws -> Words {
        let fixture = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .appendingPathComponent("../../../tests/fixtures/send_guard_words.json").standardized
        return try JSONDecoder().decode(Words.self, from: Data(contentsOf: fixture))
    }

    func testTheAttachmentWordsFireWhereThePagesDo() throws {
        let words = try words()
        for text in words.strong {
            XCTAssertFalse(SendGuard.attachWord(text, allowWeak: false).isEmpty, "strong: \(text)")
            XCTAssertFalse(SendGuard.attachWord(text, allowWeak: true).isEmpty, "strong: \(text)")
        }
        for text in words.weak {
            XCTAssertEqual(SendGuard.attachWord(text, allowWeak: false), "", "weak, in a reply: \(text)")
            XCTAssertFalse(SendGuard.attachWord(text, allowWeak: true).isEmpty, "weak, where allowed: \(text)")
        }
        for text in words.never {
            XCTAssertEqual(SendGuard.attachWord(text, allowWeak: true), "", "never: \(text)")
        }
        for text in words.replyNever {
            XCTAssertEqual(SendGuard.attachWord(text, allowWeak: false), "", "a reply about their file: \(text)")
            XCTAssertEqual(keys(snapshot(mode: .reply, subject: "Re: x", subject0: "Re: x", body: text)), [])
        }
        XCTAssertEqual(SendGuard.attachWord("Voici le devis en pièce jointe.", allowWeak: false), "en pièce jointe",
                       "echoed as typed")
    }

    func testLongUnbrokenTextIsCheckedFast() {
        let runs = ["QUJD".repeated(12500), "-".repeated(50000), "_".repeated(50000), "a-".repeated(25000),
                    ".".repeated(50000), "@".repeated(50000), "ال".repeated(25000), ", ".repeated(25000),
                    "a" + " ".repeated(49998) + "b"]
        var spent: [(text: String, seconds: Double)] = []
        for text in runs {
            let started = Self.threadSeconds()
            for mode in [API.ComposeMode.new, .reply] {
                _ = SendGuard.check(snapshot(mode: mode, body: text))
                _ = SendGuard.check(snapshot(mode: mode, subject: text))
            }
            spent.append((String(text.prefix(4)), Self.threadSeconds() - started))
        }
        // All of them take about half a second in a debug build on an M5, and under a second with the Mac busy; a
        // slower runner may take twice that. A guard that folded each character into strings of its own and tried
        // every word at every dash took six seconds here, and over ten on a busy Mac.
        let slowest = spent.max { $0.seconds < $1.seconds }!
        XCTAssertLessThan(spent.map(\.seconds).reduce(0, +), 3,
                          "the slowest, \(slowest.text)…, took \(String(format: "%.2f", slowest.seconds)) s")
    }

    /// The processor time this thread has had: a slow check shows in it as in the wall clock, but a busy machine
    /// (a build beside the tests) doesn't stretch it.
    private static func threadSeconds() -> Double {
        var now = timespec()
        clock_gettime(CLOCK_THREAD_CPUTIME_ID, &now)
        return Double(now.tv_sec) + Double(now.tv_nsec) / 1_000_000_000
    }

    func testWhatYouTypedLeavesOutSignatureAndQuote() {
        XCTAssertEqual(SendGuard.typedText("see attached\n\n-- \nAda", sigBlock: "\n\n-- \nAda"), "see attached")
        XCTAssertEqual(SendGuard.typedText("Hi there\n-- \nAda\nAttachments may contain viruses.", sigBlock: ""), "Hi there")
        XCTAssertEqual(SendGuard.typedText("Thanks\nOn Mon, 3 Feb 2026, Dana <dana@example.test> wrote:\n> see attached\nsee attached",
                                           sigBlock: ""), "Thanks")
        XCTAssertEqual(SendGuard.typedText("Noted\n-----Original Message-----\nsee attached", sigBlock: ""), "Noted")
        XCTAssertEqual(SendGuard.typedText("> see attached\nok, thanks", sigBlock: ""), "ok, thanks")
        XCTAssertEqual(SendGuard.typedText("Hi Bo,\nnotes below.\n\n-- \nAda Lovelace\n\nP.S. I attached the deck.",
                                           sigBlock: "\n\n-- \nAda Lovelace"),
                       "Hi Bo,\nnotes below.\n\n\n\nP.S. I attached the deck.", "text on either side of the signature stays")
        XCTAssertEqual(keys(snapshot(mode: .reply, subject: "Re: x", subject0: "Re: x", body: "Thanks.\n\n-- \nAda\n\nP.S. The deck is attached.",
                                     sigBlock: "\n\n-- \nAda")), ["attach"])
    }

    func testTheSubject() {
        XCTAssertEqual([SendGuard.subjectEmpty("", mode: .reply), SendGuard.subjectEmpty("  ", mode: .new),
                        SendGuard.subjectEmpty("Re:", mode: .reply), SendGuard.subjectEmpty("Fwd:", mode: .forward),
                        SendGuard.subjectEmpty("Re:", mode: .new)], [true, true, false, false, true])
        XCTAssertEqual(SendGuard.check(snapshot(subject: "", ack: .init(subject: true))), [])
        XCTAssertEqual(keys(snapshot(mode: .reply, subject: "Re: Contract attached", subject0: "Re: Contract attached")), [],
                       "an inherited subject isn't your wording")
        XCTAssertEqual(keys(snapshot(mode: .reply, subject: "Re: Contract attached, v2", subject0: "Re: Contract attached")),
                       ["attach"])
        XCTAssertEqual(keys(snapshot(subject: "Contract attached")), ["attach"])
    }

    func testAFileStillReadingIsTheOnlyLine() {
        XCTAssertEqual(SendGuard.check(snapshot(subject: "", body: "see attached", reading: ["a.pdf", "b.pdf"])),
                       [.reading(name: "a.pdf", more: 1)])
    }

    func testAPassedSendTimeStopsIt() {
        XCTAssertEqual(keys(snapshot(sendAfter: 1_000_000_003)), ["late"])
        XCTAssertEqual(keys(snapshot(sendAfter: 1_000_003_600)), [])
        XCTAssertTrue(SendGuard.hard(SendGuard.check(snapshot(sendAfter: 1_000_000_000 - 60))))
    }

    func testWhatCountsAsAttached() {
        XCTAssertEqual(keys(snapshot(mode: .forward, body: "see attached", fwdAttach: 2)), [])
        XCTAssertEqual(keys(snapshot(mode: .forward, body: "see attached", fwdAttach: 0)), ["attach"])
        XCTAssertEqual(keys(snapshot(body: "see attached", nAttach: 1)), [])
        XCTAssertEqual(keys(snapshot(body: "see attached", ack: .init(attach: true))), [])
        XCTAssertEqual(keys(snapshot(mode: .reply, subject: "Re: x", subject0: "Re: x", body: "the attachment you sent")), [])
    }

    func testReplyAllCountsEachPersonOnceAndNotYou() {
        let fan = { (s: SendGuard.Snapshot) in SendGuard.check(s).first { $0.key == "fanout" } }
        XCTAssertEqual(fan(snapshot(mode: .replyall, to: people(1), cc: Array(people(6).dropFirst()))),
                       .fanout(n: 6, removable: 0))
        XCTAssertEqual(keys(snapshot(mode: .replyall, to: people(1), cc: Array(people(5).dropFirst()))), [])
        XCTAssertEqual(keys(snapshot(mode: .replyall, to: people(1), cc: Array(people(5).dropFirst()) + ["ada@example.test"])), [])
        XCTAssertEqual(keys(snapshot(mode: .reply, to: people(4), cc: Array(people(10).dropFirst(4)))), [])
        XCTAssertEqual(keys(snapshot(mode: .replyall, to: people(1), cc: Array(people(8).dropFirst()), ack: .init(fanout: 8))), [],
                       "waved through up to that many people")
        XCTAssertEqual(keys(snapshot(mode: .replyall, to: people(1), cc: Array(people(9).dropFirst()), ack: .init(fanout: 8))),
                       ["fanout"])
        XCTAssertEqual(fan(snapshot(mode: .replyall, to: ["mira@example.test"],
                                    cc: people(4) + ["own1@example.test", "own2@example.test"],
                                    autoCc: people(6) + ["ada@example.test"])), .fanout(n: 7, removable: 4),
                       "only people still copied in can be removed")
        XCTAssertEqual(fan(snapshot(mode: .replyall, to: ["bo@example.test"], cc: people(5) + ["ada+news@example.test"],
                                    autoCc: people(5) + ["ada+news@example.test"])), .fanout(n: 6, removable: 5),
                       "your own tagged address is no one to remove")
    }

    func testYourOwnAddressHoweverItIsTagged() {
        let mine = SendGuard.mineSet(["Ada@example.test"])
        XCTAssertEqual([SendGuard.isMine("ada@example.test", mine), SendGuard.isMine("ada+news@example.test", mine),
                        SendGuard.isMine("adam@example.test", mine), SendGuard.isMine("bo+ada@example.test", mine)],
                       [true, true, false, false])
        XCTAssertEqual(["ada+news@example.test", "ada@example.test", "+only@example.test", "no-at"].map(SendGuard.untagged),
                       ["ada@example.test", "ada@example.test", "+only@example.test", "no-at"])
        let tagged = SendGuard.mineSet(["ada+work@example.test"])
        XCTAssertTrue(SendGuard.isMine("ada@example.test", tagged))
        XCTAssertTrue(SendGuard.isMine("ada+other@example.test", tagged))
        XCTAssertEqual(SendGuard.fanout(to: ["bo@example.test", "ada+news@example.test"], cc: [], mine: ["ada@example.test"]), 1)
    }

    func testAnAddressOneTypoFromSomeoneYouWriteTo() {
        let pair = SendGuard.NearMiss(addr: "jon@example.test", near: "john@example.test")
        let other = SendGuard.NearMiss(addr: "kat@example.test", near: "kate@example.test")
        let held = SendGuard.check(snapshot(near: [pair]))
        XCTAssertEqual(held, [.near(pairs: [pair])])
        XCTAssertTrue(SendGuard.blocks(held))
        XCTAssertEqual(SendGuard.check(snapshot(near: [pair, other], ack: .init(near: [pair.addr]))), [.near(pairs: [other])])
        XCTAssertEqual(keys(snapshot(near: [pair], ack: .init(near: [pair.addr]))), [])
        let both = SendGuard.check(snapshot(fresh: [pair.addr, "new@example.test"], near: [pair]))
        XCTAssertEqual(both, [.near(pairs: [pair]), .new(addrs: ["new@example.test"])])
        let line = GuardWarning.near(pairs: [pair]).line()
        XCTAssertEqual(line.text, "“jon@example.test” is one typo away from john@example.test, someone you’ve written to.")
        XCTAssertEqual(line.fix, GuardFix(kind: "near", label: "Use john@example.test",
                                          swap: .init(from: pair.addr, to: pair.near)))
        XCTAssertTrue(GuardWarning.near(pairs: [pair, other]).line().text.hasSuffix("1 more looks the same."))
    }

    func testAReplyItsReplyToSendsElsewhere() {
        let away = "desk@elsewhere.example.test", bo = "bo@example.test"
        let redirect = SendGuard.Snapshot.Redirect(to: away, sender: bo, stranger: true)
        func onIt(to: [String] = [away], cc: [String] = [], mine: [String] = ["ada@example.test"], fresh: [String] = [],
                  redirect r: SendGuard.Snapshot.Redirect = redirect, ack: SendGuard.Acks = .init()) -> [GuardWarning]
        {
            SendGuard.check(snapshot(mode: .reply, subject: "Re: x", subject0: "Re: x", to: to, cc: cc, mine: mine,
                                     fresh: fresh, redirect: r, ack: ack))
        }
        XCTAssertEqual(onIt(), [.redirect(to: away, sender: bo, block: .confirm)])
        XCTAssertEqual(onIt(redirect: .init(to: away, sender: bo, stranger: false)), [.redirect(to: away, sender: bo, block: .soft)])
        XCTAssertFalse(SendGuard.blocks(onIt(ack: .init(redirect: true))))
        XCTAssertEqual(onIt(to: [bo]), [])
        XCTAssertEqual(onIt(to: [away, bo]), [], "the sender is on it too")
        XCTAssertEqual(onIt(cc: [bo]), [])
        XCTAssertEqual(onIt(mine: [away]), [])
        XCTAssertEqual(onIt(to: ["desk+tag@elsewhere.example.test"], mine: [away],
                            redirect: .init(to: "desk+tag@elsewhere.example.test", sender: bo, stranger: true)), [])
        XCTAssertEqual(onIt(fresh: [away, "new@example.test"]).map(\.key), ["redirect", "new"])
        let line = GuardWarning.redirect(to: away, sender: bo, block: .confirm).line()
        XCTAssertEqual(line.text, "Replies go to desk@elsewhere.example.test, not to the sender, bo@example.test.")
        XCTAssertEqual(line.fix, GuardFix(kind: "sender", label: "Reply to the sender instead", swap: .init(from: away, to: bo)))
    }

    func testSomeoneNewAloneNeverStopsASend() {
        let found = SendGuard.check(snapshot(fresh: ["new@example.test"]))
        XCTAssertEqual(found.map(\.key), ["new"])
        XCTAssertFalse(SendGuard.blocks(found))
        XCTAssertTrue(SendGuard.blocks(SendGuard.check(snapshot(subject: "", fresh: ["new@example.test"]))))
    }

    func testListsAndLinesInThePagesWords() {
        XCTAssertEqual([[], ["a"], ["a", "b"], ["a", "b", "c"], ["a", "b", "c", "d"]].map(SendGuard.list),
                       ["", "a", "a and b", "a, b and c", "a, b and 2 more"])
        let lines: [(GuardWarning, String, GuardFix?)] = [
            (.reading(name: "a.pdf", more: 0), "Still adding a.pdf. Send again once it’s added.", nil),
            (.reading(name: "a.pdf", more: 2), "Still adding a.pdf and 2 more. Send again once they’re added.", nil),
            (.subject, "There’s no subject.", GuardFix(kind: "subject", label: "Add a subject")),
            (.attach(word: "pièce jointe"), "You wrote “pièce jointe”, but nothing is attached.",
             GuardFix(kind: "attach", label: "Attach a file")),
            (.fanout(n: 7, removable: 6), "This reply goes to 7 people.",
             GuardFix(kind: "trim", label: "Remove the 6 copied from the original")),
            (.fanout(n: 7, removable: 0), "This reply goes to 7 people.", GuardFix(kind: "review", label: "Review recipients")),
            (.new(addrs: ["a@example.test", "b@example.test"]), "New to you: a@example.test and b@example.test.", nil),
            (.unchecked, "The checks before sending couldn’t run.", nil),
            (.norcpt, "Add at least one recipient.", GuardFix(kind: "to", label: "Add one")),
            (.bad(value: "bo@", more: 0, field: .to), "“bo@” isn’t a valid address.", GuardFix(kind: "bad", label: "Edit it")),
            (.bad(value: "bo@", more: 1, field: .to), "“bo@” isn’t a valid address. 1 more needs fixing too.",
             GuardFix(kind: "bad", label: "Edit it")),
            (.bad(value: "bo@", more: 2, field: .to), "“bo@” isn’t a valid address. 2 more need fixing too.",
             GuardFix(kind: "bad", label: "Edit it")),
        ]
        for (warning, text, fix) in lines {
            XCTAssertEqual(warning.line().text, text)
            XCTAssertEqual(warning.line().fix, fix, text)
        }
        XCTAssertEqual(GuardWarning.late(when: 5).line(when: { _ in "today 6:00 PM" }).text,
                       "The time you picked, today 6:00 PM, has passed.")
    }

    func testTheChecksViewSaysScheduleAndHasNoWayPastAHardLine() {
        let soft = GuardView.of([.subject], sendAfter: 100, shownAt: 0, when: { _ in "" })
        XCTAssertFalse(soft.hard)
        XCTAssertTrue(soft.said.contains("to schedule anyway"), soft.said)
        let hard = GuardView.of([.norcpt], sendAfter: 0, shownAt: 0, when: { _ in "" })
        XCTAssertTrue(hard.hard)
        XCTAssertTrue(hard.said.hasSuffix("Press Escape to keep editing."))
        let mixed = GuardView.of([.new(addrs: ["a@x.test"]), .subject], sendAfter: 0, shownAt: 0, when: { _ in "" })
        XCTAssertEqual(mixed.keys, ["subject"], "a soft line is nothing to wave through")
        XCTAssertTrue(mixed.lines[0].soft)
    }
}

private extension String {
    func repeated(_ times: Int) -> String { String(repeating: self, count: times) }
}

/// Links pasted into the composer, as the page's `linkClean.test.ts` holds them.
final class LinkCleanTests: XCTestCase {
    private func clean(_ text: String) -> String { LinkClean.clean(text) }

    func testTrackingParametersAreDropped() {
        let cases = [
            ("https://shop.example.test/a?utm_source=news&utm_medium=email", "https://shop.example.test/a"),
            ("https://shop.example.test/a?id=7&utm_campaign=x&page=2", "https://shop.example.test/a?id=7&page=2"),
            ("https://shop.example.test/a?UTM_Source=x", "https://shop.example.test/a"),
            ("https://shop.example.test/a?fbclid=abc", "https://shop.example.test/a"),
            ("https://shop.example.test/a?gclid=abc&msclkid=def&id=1", "https://shop.example.test/a?id=1"),
            ("https://shop.example.test/a?utm_source=x#frag", "https://shop.example.test/a#frag"),
            ("https://shop.example.test/a?id=1&utm_id=2#frag", "https://shop.example.test/a?id=1#frag"),
        ]
        for (link, want) in cases { XCTAssertEqual(clean(link), want) }
    }

    func testTheRestOfALinkStaysAsWritten() {
        XCTAssertEqual(clean("https://shop.example.test/a?q=hello%20world&sig=a+b%2Fc&utm_source=x"),
                       "https://shop.example.test/a?q=hello%20world&sig=a+b%2Fc")
        let lookalikes = "https://shop.example.test/a?utm=1&utmost=2&fbclids=3&ref=4&source=5&si=6"
        XCTAssertEqual(clean(lookalikes), lookalikes)
        for link in ["https://shop.example.test/", "http://SHOP.example.test/A%20b", "https://shop.example.test/a?b=1#c",
                     "http://SHOP.example.test/A%20b?x=%7Ey", "https://app.example.test/#/page?utm_source=x"]
        {
            XCTAssertEqual(clean(link), link, "down to the byte")
        }
    }

    func testRedirectWrappersComeOff() {
        let target = "https://real.example.test/page?id=1"
        let e = target.addingPercentEncoding(withAllowedCharacters: .alphanumerics)!
        for link in ["https://www.google.com/url?q=\(e)&sa=D&ust=1", "https://www.google.co.uk/url?url=\(e)",
                     "https://l.facebook.com/l.php?u=\(e)&h=AT0x", "https://lm.facebook.com/l.php?u=\(e)&h=AT0x",
                     "https://l.instagram.com/?u=\(e)&e=AT0", "https://www.linkedin.com/redir/redirect?url=\(e)&urlhash=x",
                     "https://www.linkedin.com/safety/go?url=\(e)&trk=x",
                     "https://nam04.safelinks.protection.outlook.com/?url=\(e)&data=05%7C01%7C&sdata=x&reserved=0",
                     "https://www.youtube.com/redirect?q=\(e)&v=x", "https://slack-redir.net/link?url=\(e)"]
        {
            XCTAssertEqual(clean(link), target, link)
        }
        let inner = "https://real.example.test/page?utm_source=x&id=1"
        let google = "https://www.google.com/url?q=\(inner.addingPercentEncoding(withAllowedCharacters: .alphanumerics)!)"
        let outlook = "https://eur01.safelinks.protection.outlook.com/?url=\(google.addingPercentEncoding(withAllowedCharacters: .alphanumerics)!)&data=x"
        XCTAssertEqual(clean(outlook), "https://real.example.test/page?id=1", "layer by layer, and the inside cleaned")
        var deep = "https://real.example.test/"
        for _ in 0..<6 { deep = "https://www.google.com/url?q=\(deep.addingPercentEncoding(withAllowedCharacters: .alphanumerics)!)" }
        XCTAssertTrue(clean(deep).contains("google.com/url"), "so many layers and no more")
    }

    func testAWrapperStaysWhenItsTargetIsNoLinkOrTheHostOnlyLooksLikeOne() {
        for link in ["https://www.google.com/url?q=javascript:alert(1)", "https://www.google.com/url?q=not-a-link",
                     "https://www.google.com/url?q=https%3A%2F%2F%5Bbad",
                     "https://www.google.com/url?q=https%3A%2F%2Freal.example.test%2Fa%20b",
                     "https://www.google.com/url?sa=D", "https://www.google.com/search?q=https%3A%2F%2Freal.example.test%2F",
                     "https://google.example.test/url?q=https%3A%2F%2Freal.example.test%2F",
                     "https://notgoogle.com/url?q=https%3A%2F%2Freal.example.test%2F",
                     "https://l.facebook.com/other?u=https%3A%2F%2Freal.example.test%2F"]
        {
            XCTAssertEqual(clean(link), link)
        }
    }

    func testInPastedTextEachLinkIsCleanedAndTheWordsStay() {
        XCTAssertEqual(clean("See https://a.example.test/?utm_source=x and http://b.example.test/?fbclid=y for more."),
                       "See https://a.example.test/ and http://b.example.test/ for more.")
        XCTAssertEqual(clean("(see https://a.example.test/x?utm_source=y)."), "(see https://a.example.test/x).")
        XCTAssertEqual(clean("See https://a.example.test/x?utm_source=y, then"), "See https://a.example.test/x, then")
        XCTAssertEqual(clean("Look: https://a.example.test/x?utm_source=y!"), "Look: https://a.example.test/x!")
        XCTAssertEqual(clean("見て https://a.example.test/x?utm_source=y。"), "見て https://a.example.test/x。")
        XCTAssertEqual(clean("https://a.example.test/Foo_(bar)"), "https://a.example.test/Foo_(bar)")
        XCTAssertEqual(clean("https://a.example.test/Foo_(bar)?utm_source=x"), "https://a.example.test/Foo_(bar)")
        XCTAssertEqual(clean("<https://a.example.test/?utm_source=x>"), "<https://a.example.test/>")
        XCTAssertEqual(clean("no links here, utm_source=x"), "no links here, utm_source=x")
        XCTAssertEqual(clean(""), "")
    }

    func testALongPasteStaysFast() {
        let long = "https://a.example.test/?utm_source=x " + "word ".repeated(40000) + "https://b.example.test/"
            + ".".repeated(50000)
        let started = Date()
        XCTAssertTrue(clean(long).hasPrefix("https://a.example.test/ word"))
        XCTAssertLessThan(Date().timeIntervalSince(started), 2)
    }
}

/// Addresses, seeds, snippets, pictures and times, as the page's `model.test.ts` holds them.
final class ComposeModelTests: XCTestCase {
    func testAddresses() {
        XCTAssertEqual(Addresses.parse(#"Bea <bea@example.test>, "Cy, Jr" <cy@example.test>"#), ["bea@example.test", "cy@example.test"])
        XCTAssertEqual(Addresses.first("nobody"), "")
        XCTAssertEqual(Addresses.recipient("spoof@evil.test <Real@Example.test>"), "real@example.test",
                       "where a chip's mail goes is its <…>, not an address in its name")
        XCTAssertEqual(Addresses.recipient("BEA@example.test"), "bea@example.test")
        XCTAssertTrue(Addresses.looksLikeAddress("bea@example.test"))
        XCTAssertTrue(Addresses.looksLikeAddress("Bea <bea@example.test>"))
        XCTAssertFalse(Addresses.looksLikeAddress("bea"))
        XCTAssertFalse(Addresses.looksLikeAddress("bea@example"))
        XCTAssertTrue(Recipient("nope").bad)
        XCTAssertEqual(Addresses.split(" a@x.test, b@x.test;; "), ["a@x.test", "b@x.test"])
    }

    func testSubjectsArePrefixedOnce() {
        XCTAssertEqual(Addresses.prefixed("Lunch", "Re:"), "Re: Lunch")
        XCTAssertEqual(Addresses.prefixed("re: Lunch", "Re:"), "Re: Lunch")
        XCTAssertEqual(Addresses.prefixed("Fwd: Fabric samples", "Re:"), "Re: Fwd: Fabric samples",
                       "a reply keeps the forward's prefix, as Mail does")
        XCTAssertEqual(Addresses.prefixed("Re: Plans", "Fwd:"), "Fwd: Re: Plans", "and a forward the reply's")
        XCTAssertEqual(Addresses.prefixed("FW: Plans", "Fwd:"), "Fwd: Plans")
        XCTAssertEqual(Addresses.prefixed("", "Fwd:"), "Fwd:")
        XCTAssertEqual(Addresses.prefixed(nil, "Re:"), "Re:")
    }

    func testAReplyKnowsWhereItsReplyToSendsIt() {
        XCTAssertEqual(Addresses.redirect(replyTo: "Desk <Desk@elsewhere.example.test>", sender: "Bo@example.test"),
                       Redirect(to: "desk@elsewhere.example.test", sender: "bo@example.test"))
        XCTAssertNil(Addresses.redirect(replyTo: "bo@example.test", sender: "BO@example.test"))
        XCTAssertNil(Addresses.redirect(replyTo: "", sender: "bo@example.test"))
        XCTAssertNil(Addresses.redirect(replyTo: "desk@elsewhere.example.test", sender: nil))
        let mine = SendGuard.mineSet(["ada@example.test"])
        XCTAssertEqual(Seeds.reply(opened(replyTo: "desk@elsewhere.example.test"), all: false, mine: mine).redirect,
                       Redirect(to: "desk@elsewhere.example.test", sender: "bo@example.test"))
        XCTAssertNil(Seeds.reply(opened(), all: false, mine: mine).redirect)
    }

    func testAReplyAllLeavesYourTagsOut() {
        let seed = Seeds.reply(opened(to: "ada+news@example.test, cy@example.test", cc: "ada@example.test"), all: true,
                               mine: SendGuard.mineSet(["ada@example.test"]))
        XCTAssertEqual(seed.to, ["bo@example.test"])
        XCTAssertEqual(seed.cc, ["cy@example.test"])
        XCTAssertEqual(seed.mode, .replyall)
        XCTAssertEqual(seed.subject, "Re: Plans")
        XCTAssertEqual(seed.origKey, 7)
    }

    func testAReplyToYourOwnMessageGoesToWhomItWentTo() {
        let mine = SendGuard.mineSet(["ada@example.test"])
        let sent = opened(from: "ada@example.test", to: "Lin Mori <lin@example.test>, \"Mori, Ren\" <ren@example.test>",
                          cc: "Ada <ada+news@example.test>, cy@example.test", replyTo: "ada@example.test")
        let reply = Seeds.reply(sent, all: false, mine: mine)
        XCTAssertEqual(reply.to, ["lin@example.test", "ren@example.test"], "a follow-up goes on to them, never to you")
        XCTAssertEqual(reply.cc, [])
        XCTAssertNil(reply.redirect, "your own Reply-To sends nothing elsewhere")
        XCTAssertEqual(reply.mode, .reply)

        let all = Seeds.reply(sent, all: true, mine: mine)
        XCTAssertEqual(all.to, ["lin@example.test", "ren@example.test"])
        XCTAssertEqual(all.cc, ["cy@example.test"], "Reply All copies its Cc in, your tagged address left out")
        XCTAssertEqual(all.autoCc, ["cy@example.test"])

        let copied = Seeds.reply(opened(from: "ada@example.test", to: "ada@example.test", cc: "cy@example.test"),
                                 all: false, mine: mine)
        XCTAssertEqual(copied.to, ["cy@example.test"], "one that went to you, Cy copied in, goes to Cy")

        XCTAssertEqual(Seeds.reply(opened(from: "ada@example.test", to: "ada@example.test"), all: false, mine: mine).to,
                       ["ada@example.test"], "a note to yourself is answered as any other")
        XCTAssertEqual(Seeds.reply(opened(), all: false, mine: mine).to, ["bo@example.test"], "a message to you")
    }

    func testAHeadersPeopleAsTheyWereWritten() {
        XCTAssertEqual(Addresses.entries("Lin Mori <lin@example.test>, \"Mori, Ren\" <ren@example.test>; cy@example.test"),
                       ["Lin Mori <lin@example.test>", "\"Mori, Ren\" <ren@example.test>", "cy@example.test"])
        XCTAssertEqual(Addresses.entries(""), [])
        XCTAssertEqual(Addresses.entries("undisclosed-recipients:;"), [], "a group with no one in it")
        XCTAssertEqual(Addresses.name("\"Mori, Ren\" <ren@example.test>"), "Mori, Ren")
        XCTAssertEqual(Addresses.name("Lin Mori <lin@example.test>"), "Lin Mori")
        XCTAssertNil(Addresses.name("cy@example.test"))
        XCTAssertNil(Addresses.name("<cy@example.test>"))
        XCTAssertNil(Addresses.name("cy@example.test <cy@example.test>"), "an address written as a name is none")
    }

    func testAForwardCountsTheFilesThatTravelWithinTheBudget() {
        let file = { (size: Int, travels: Bool) in
            API.AttachmentPart(contentType: "application/pdf", filename: "f.pdf", isInline: 0, partId: "2", size: size,
                               travels: travels)
        }
        let seed = Seeds.forward(opened(subject: "x", attachments: [file(3, true), file(9, false), file(2, true)]), budget: 1000)
        XCTAssertEqual(seed.fwdAttach, 2)
        XCTAssertEqual(seed.subject, "Fwd: x")
        XCTAssertEqual(Seeds.quoteLabel(mode: .forward, fwdAttach: 2, open: false), "▸ forwarded message · 2 attachments")
        XCTAssertEqual(Seeds.quoteLabel(mode: .reply, fwdAttach: 0, open: true), "▾ quoted message")
        XCTAssertEqual(Seeds.filesForwarded([file(40, true), file(70, true), file(1, true)], budget: 100), 1,
                       "nothing past where the budget ends")
    }

    func testQuickRepliesAreForPeopleYouCouldAnswer() {
        let mine = SendGuard.mineSet(["ada@example.test"])
        XCTAssertTrue(QuickReply.offered(to: "bea@example.test", mine: mine))
        XCTAssertFalse(QuickReply.offered(to: "ada@example.test", mine: mine))
        XCTAssertFalse(QuickReply.offered(to: "ada+news@example.test", mine: mine))
        XCTAssertFalse(QuickReply.offered(to: "no-reply@example.test", mine: mine))
        XCTAssertFalse(QuickReply.offered(to: "notifications@example.test", mine: mine))
        XCTAssertFalse(QuickReply.offered(to: "bea@example.test", mine: mine, isJunk: true))
        XCTAssertFalse(QuickReply.offered(to: "bea@example.test", mine: mine, bucket: "receipts"))
        XCTAssertFalse(QuickReply.offered(to: "", mine: mine))
    }

    func testFourTimesToSendLaterAllAhead() {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "Europe/Berlin")!
        let noon = calendar.date(from: DateComponents(year: 2026, month: 11, day: 2, hour: 12))!   // a Monday
        let presets = SendTimes.presets(now: noon, calendar: calendar)
        XCTAssertEqual(presets.map(\.label), ["In 1 hour", "This evening", "Tomorrow 8 AM", "Monday 8 AM"])
        XCTAssertTrue(presets.allSatisfy { $0.epoch > Int(noon.timeIntervalSince1970) })
        let monday = Date(timeIntervalSince1970: TimeInterval(presets[3].epoch))
        XCTAssertEqual(calendar.component(.weekday, from: monday), 2)
        XCTAssertEqual(calendar.component(.day, from: monday), 9, "the next Monday, not today")
        XCTAssertTrue(SendTimes.words(Int(noon.timeIntervalSince1970) + 3 * 3600, now: noon, calendar: calendar).hasPrefix("today "))
        XCTAssertTrue(SendTimes.words(Int(noon.timeIntervalSince1970) + 20 * 3600, now: noon, calendar: calendar).hasPrefix("tomorrow "))
    }

    /// This evening is offered while 18:00 today is still ahead, and not once it has passed, whatever the zone: at
    /// 20:26 it had named tomorrow at 18:00.
    func testThisEveningIsOfferedOnlyWhileSixIsStillAhead() {
        for zone in ["Europe/Berlin", "America/Los_Angeles", "Asia/Kolkata", "Pacific/Auckland"] {
            var calendar = Calendar(identifier: .gregorian)
            calendar.timeZone = TimeZone(identifier: zone)!
            // Friday 20 November 2026 at `hour`:`minute` on that zone's clock.
            func at(_ hour: Int, _ minute: Int) -> Date {
                calendar.date(from: DateComponents(year: 2026, month: 11, day: 20, hour: hour, minute: minute))!
            }
            for (hour, minute) in [(0, 1), (12, 0), (17, 0), (17, 59)] {
                let presets = SendTimes.presets(now: at(hour, minute), calendar: calendar)
                XCTAssertEqual(presets.map(\.label), ["In 1 hour", "This evening", "Tomorrow 8 AM", "Monday 8 AM"],
                               "\(zone) at \(hour):\(minute)")
                XCTAssertEqual(presets[1].epoch, Int(at(18, 0).timeIntervalSince1970), "today at 18:00, in \(zone)")
            }
            for (hour, minute) in [(18, 0), (18, 1), (19, 0), (20, 26), (23, 59)] {
                let now = at(hour, minute)
                let presets = SendTimes.presets(now: now, calendar: calendar)
                XCTAssertEqual(presets.map(\.label), ["In 1 hour", "Tomorrow 8 AM", "Monday 8 AM"], "\(zone) at \(hour):\(minute)")
                XCTAssertTrue(presets.allSatisfy { $0.epoch > Int(now.timeIntervalSince1970) }, "\(zone) at \(hour):\(minute)")
                let tomorrow = Date(timeIntervalSince1970: TimeInterval(presets[1].epoch))
                XCTAssertEqual(calendar.dateComponents([.day, .hour], from: tomorrow), DateComponents(day: 21, hour: 8),
                               "\(zone) at \(hour):\(minute)")
            }
        }
    }

    func testAPicturesMark() {
        XCTAssertEqual(Pictures.mark("a]b[c\nd.png", cid: "tok"), "![a b c d.png](cid:tok)")
        XCTAssertEqual(Pictures.withoutMark("a ![x](cid:one) b ![y](cid:two) ![z](cid:one)", cid: "one"), "a  b ![y](cid:two) ")
        XCTAssertTrue(Pictures.isPicture("image/png"))
        XCTAssertTrue(Pictures.isPicture("IMAGE/JPEG"))
        XCTAssertFalse(Pictures.isPicture("image/svg+xml"))
        let picture = ComposeFile(filename: "a", contentType: "image/png", size: 1, base64: "QQ==", cid: "tok")
        let unmarked = ComposeFile(filename: "a", contentType: "image/png", size: 1, base64: "QQ==", cid: nil)
        let text = ComposeFile(filename: "b", contentType: "text/plain", size: 1, base64: "Qg==", cid: "doc")
        XCTAssertEqual(Pictures.parts([unmarked, picture, text]), [API.PreviewPicture(cid: "tok", contentType: .imagePng)])
        XCTAssertEqual(Pictures.inline(#"<img src="cid:tok@mailmind"><img src="cid:other@mailmind">"#, files: [picture, text]),
                       #"<img src="data:image/png;base64,QQ=="><img src="cid:other@mailmind">"#)
    }

    func testASnippetsWord() {
        func word(_ text: String, _ caret: Int) -> [String]? {
            Snippets.wordBefore(text, caret: caret).map { [String($0.from), $0.word] }
        }
        XCTAssertEqual(word(";sig", 4), ["0", "sig"])
        XCTAssertEqual(word("a\n;sig b", 6), ["2", "sig"])
        XCTAssertEqual(word("a ;sig", 4), ["2", "s"])
        XCTAssertEqual(word(";", 1), ["0", ""])
        for (text, caret) in [("a;sig", 5), ("sig", 3), ("; x", 3), ("", 0), (";sig", 0)] {
            XCTAssertNil(word(text, caret), text)
        }
        let a = API.Snippet(text: "1", trigger: "ab"), b = API.Snippet(text: "2", trigger: "abc"), c = API.Snippet(text: "3", trigger: "x")
        XCTAssertEqual(Snippets.offered([b, a, c], word: "AB"), a, "the very name first")
        XCTAssertNil(Snippets.offered([a, b, c], word: "a"))
        XCTAssertEqual(Snippets.offered([a, b, c], word: "abc"), b)
        XCTAssertEqual(Snippets.offered([a, c], word: "a"), a, "the only one it starts")
        XCTAssertNil(Snippets.offered([a], word: ""))
        XCTAssertNil(Snippets.offered([a], word: "b"))
    }

    func testAPlaceholder() {
        let text = "a {{one}} b {{two words}} c"
        let span = { (range: NSRange?) in range.map { [$0.location, NSMaxRange($0)] } }
        XCTAssertEqual(span(Snippets.after(text, at: 0)), [2, 9])
        XCTAssertEqual(span(Snippets.after(text, at: 2)), [2, 9])
        XCTAssertEqual(span(Snippets.after(text, at: 3)), [12, 25])
        XCTAssertNil(Snippets.after(text, at: 13))
        XCTAssertEqual(span(Snippets.before(text, at: 27)), [12, 25])
        XCTAssertEqual(span(Snippets.before(text, at: 25)), [12, 25])
        XCTAssertEqual(span(Snippets.before(text, at: 24)), [2, 9])
        XCTAssertNil(Snippets.before(text, at: 8))
        for none in ["{{}}", "{one}", "{{a\nb}}", "{{{a}", "{{a}b}}", "{{\(String(repeating: "x", count: 65))}}"] {
            XCTAssertNil(Snippets.after(none, at: 0), none)
        }
        XCTAssertEqual(span(Snippets.after("{{\(String(repeating: "x", count: 64))}}", at: 0)), [0, 68])
    }
}

/// A recipient field's completion list, as a person keys it: ↑ and ↓ move the highlight, Return or Tab takes it,
/// a word that is no address takes the first suggestion, a whole address typed is kept as typed, and Esc takes the
/// list away, not the composer.
final class RecipientCompletionTests: XCTestCase {
    private let lin = API.Suggestion(addr: "lin@example.test", n: 4, name: "Lin Mori")
    private let linus = API.Suggestion(addr: "linus@example.test", n: 1, name: "")
    private let bo = API.Suggestion(addr: "Bo@example.test", n: 2, name: "Bo")

    func testAWordThatIsNoAddressHighlightsTheFirstSoReturnTakesIt() {
        var list = RecipientCompletion()
        list.show([lin, linus], for: "lin", holding: [])
        XCTAssertTrue(list.shown)
        XCTAssertEqual(list.at, 0)
        XCTAssertEqual(list.taken(typed: "lin"), .suggestion("Lin Mori <lin@example.test>"),
                       "not a chip that says lin, but the person by name")
    }

    /// A suggestion taken makes a chip that shows the person's name, as the list did, and goes to their address; a
    /// name an address list would read otherwise is quoted, and a suggestion with no name is its address.
    func testAChosenSuggestionsChipShowsTheName() {
        XCTAssertEqual(RecipientCompletion.chip(lin), "Lin Mori <lin@example.test>")
        XCTAssertEqual(RecipientCompletion.chip(linus), "linus@example.test")
        let ren = API.Suggestion(addr: "ren@example.test", n: 1, name: "Mori, Ren")
        XCTAssertEqual(RecipientCompletion.chip(ren), #""Mori, Ren" <ren@example.test>"#)
        XCTAssertEqual(Addresses.named(#"Ren "R" Mori"#, "ren@example.test"), #""Ren \"R\" Mori" <ren@example.test>"#)
        XCTAssertEqual(Addresses.named("ren@example.test", "ren@example.test"), "ren@example.test")
        XCTAssertEqual(ChipField.shown(RecipientCompletion.chip(lin)), "Lin Mori")
        XCTAssertEqual(ChipField.shown(RecipientCompletion.chip(ren)), "Mori, Ren")
        // Its chip is one recipient, comma and all, going where the suggestion does.
        let chips = Addresses.split(RecipientCompletion.chip(ren)).map { Recipient($0) }
        XCTAssertEqual(chips.map(\.address), ["ren@example.test"])
        XCTAssertFalse(chips[0].bad)
    }

    /// A name comes from other people's headers, and both servers refuse a header that runs over lines: each control
    /// character and line separator in it is a space, a run of them one, before it is quoted.
    func testASuggestionsNameIsOneLine() {
        XCTAssertEqual(Addresses.named("Lin\r\nMori", "lin@example.test"), "Lin Mori <lin@example.test>")
        XCTAssertEqual(Addresses.named("Lin\nMori\r", "lin@example.test"), "Lin Mori <lin@example.test>")
        XCTAssertEqual(Addresses.named("Lin\t\tMori", "lin@example.test"), "Lin Mori <lin@example.test>")
        XCTAssertEqual(Addresses.named("Lin\u{0}Mori", "lin@example.test"), "Lin Mori <lin@example.test>")
        XCTAssertEqual(Addresses.named("Lin\u{2028}Mori\u{2029}", "lin@example.test"), "Lin Mori <lin@example.test>")
        XCTAssertEqual(Addresses.named("Lin \u{85} \u{1B}Mori", "lin@example.test"), "Lin Mori <lin@example.test>")
        XCTAssertEqual(Addresses.named("Mori,\r\n Ren", "ren@example.test"), #""Mori, Ren" <ren@example.test>"#,
                       "folded before it is quoted")
        XCTAssertEqual(Addresses.named("\r\n\t\u{0}", "ren@example.test"), "ren@example.test", "nothing but breaks")
        XCTAssertEqual(Addresses.named("Zoë 👩‍👩‍👧 Mori", "zoe@example.test"), "Zoë 👩‍👩‍👧 Mori <zoe@example.test>",
                       "a joiner is no control character")
        // The chip a suggestion with such a name makes is one line, going to the suggestion's address.
        let broken = API.Suggestion(addr: "lin@example.test", n: 1, name: "Lin\r\nMori")
        XCTAssertEqual(RecipientCompletion.chip(broken), "Lin Mori <lin@example.test>")
        XCTAssertEqual(Addresses.split(RecipientCompletion.chip(broken)).map { Recipient($0).address },
                       ["lin@example.test"])
    }

    func testAWholeAddressTypedIsKeptAsTyped() {
        var list = RecipientCompletion()
        list.show([linus], for: "lin@example.test", holding: [])
        XCTAssertEqual(list.at, -1, "nothing highlighted over an address")
        XCTAssertEqual(list.taken(typed: "lin@example.test"), .typed)
        XCTAssertTrue(list.move(1))
        XCTAssertEqual(list.taken(typed: "lin@example.test"), .suggestion("linus@example.test"),
                       "unless one is highlighted")
    }

    func testTheArrowsGoRoundTheList() {
        var list = RecipientCompletion()
        list.show([lin, linus, bo], for: "x", holding: [])
        XCTAssertEqual(list.at, 0)
        XCTAssertTrue(list.move(1))
        XCTAssertEqual(list.chosen?.addr, "linus@example.test")
        XCTAssertTrue(list.move(1))
        XCTAssertTrue(list.move(1))
        XCTAssertEqual(list.at, 0, "round from the last to the first")
        XCTAssertTrue(list.move(-1))
        XCTAssertEqual(list.at, 2, "and back from the first to the last")

        var none = RecipientCompletion()
        none.show([lin, linus], for: "lin@example.test", holding: [])
        XCTAssertTrue(none.move(-1))
        XCTAssertEqual(none.at, 1, "↑ with none highlighted starts at the last")
        var empty = RecipientCompletion()
        XCTAssertFalse(empty.move(1), "with no list the arrows are the field's")
    }

    func testThoseChosenAlreadyAreLeftOut() {
        var list = RecipientCompletion()
        list.show([lin, bo], for: "o", holding: [Recipient("bo@example.test")])
        XCTAssertEqual(list.items.map(\.addr), ["lin@example.test"])
        list.show([bo], for: "bo", holding: [Recipient("Bo <BO@example.test>")])
        XCTAssertFalse(list.shown)
        XCTAssertEqual(list.at, -1)
        XCTAssertEqual(list.taken(typed: "bo"), .typed, "no list: what is typed goes, as the page's field has it")
        XCTAssertEqual(list.taken(typed: "  "), .nothing, "nothing typed: Return is the field's own")
    }

    func testEscTakesTheListAwayAndOnlyTheList() {
        var list = RecipientCompletion()
        list.show([lin], for: "lin", holding: [])
        XCTAssertTrue(list.escape(), "Esc is taken: the composer stays")
        XCTAssertFalse(list.shown)
        XCTAssertEqual(list.at, -1)
        XCTAssertFalse(list.escape(), "with no list, Esc goes on to whatever else answers it")
    }
}
