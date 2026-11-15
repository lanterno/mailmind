import XCTest

/// What the compose window, Settings and a sender's card say in Meadow's look: the head's title, the From chip, the
/// foot's line, a chip's name, Settings' places and a card's name.
@MainActor
final class MeadowDialogTests: XCTestCase {
    func testTheComposersHeadSaysWhatIsWrittenAndToWhom() {
        XCTAssertEqual(ComposerForm.heading(.new, to: []), "New message")
        XCTAssertEqual(ComposerForm.heading(.forward, to: [Recipient("bo@example.test")]), "Forward")
        XCTAssertEqual(ComposerForm.heading(.reply, to: [Recipient("Bo Brown <bo@example.test>")]), "Reply to Bo")
        XCTAssertEqual(ComposerForm.heading(.replyall, to: [Recipient("\"Dee Ash\" <dee@example.test>"),
                                                            Recipient("bo@example.test")]), "Reply to Dee")
        XCTAssertEqual(ComposerForm.heading(.reply, to: [Recipient("bo@example.test")]), "Reply to bo@example.test",
                       "an address with no name is said whole")
        XCTAssertEqual(ComposerForm.heading(.reply, to: []), "Reply")
        XCTAssertEqual(ComposerForm.heading(.replyall, to: []), "Reply all")
    }

    func testTheFromChipNamesTheAccountByItsAlias() {
        let aliases: [String: String?] = ["hana@example.test": "Work", "home@example.test": "", "x@example.test": nil]
        XCTAssertEqual(ComposerForm.fromWords("hana@example.test", aliases), "Work · hana@example.test")
        XCTAssertEqual(ComposerForm.fromWords("home@example.test", aliases), "home@example.test")
        XCTAssertEqual(ComposerForm.fromWords("x@example.test", aliases), "x@example.test")
        XCTAssertEqual(ComposerForm.fromWords("new@example.test", aliases), "new@example.test")
    }

    func testTheFootSaysASnippetsOfferFirstThenTheSave() {
        XCTAssertEqual(ComposerForm.footLine(savedLine: "Draft saved", offer: "sig"), "Tab brings in ;sig")
        XCTAssertEqual(ComposerForm.footLine(savedLine: "Draft saved", offer: nil), "Draft saved")
        XCTAssertEqual(ComposerForm.footLine(savedLine: "", offer: nil), "Saves once you type")
    }

    func testAChipShowsTheNameBeforeTheAddress() {
        XCTAssertEqual(ChipField.shown("Bo Brown <bo@example.test>"), "Bo Brown")
        XCTAssertEqual(ChipField.shown("\"Dee Ash\" <dee@example.test>"), "Dee Ash")
        XCTAssertEqual(ChipField.shown("  bo@example.test "), "bo@example.test")
        XCTAssertEqual(ChipField.shown("not an address"), "not an address")
    }

    func testSettingsListsItsPlacesInOrderEachWithItsWords() {
        XCTAssertEqual(SettingsTab.order, [.accounts, .general, .notifications, .ai])
        XCTAssertEqual(SettingsTab.order.map(\.title), ["Accounts", "General", "Notifications", "AI"])
        XCTAssertEqual(Set(SettingsTab.order.map(\.lede)).count, SettingsTab.order.count)
        XCTAssertFalse(SettingsTab.order.contains { $0.lede.isEmpty })
    }

    func testASendersCardIsNamedForThemByTheirFirstName() {
        func card(_ name: String) -> API.ContactCard {
            API.ContactCard(accounts: [], addr: "aoi@example.test", count: 1, known: false, name: name, ok: true,
                            recent: [], replied: 0)
        }
        XCTAssertEqual(ContactCard.heading(card("Aoi Nakamura")), "About Aoi")
        XCTAssertEqual(ContactCard.heading(card("")), "About this sender")
        XCTAssertEqual(ContactCard.heading(card("aoi@example.test")), "About this sender")
        XCTAssertEqual(ContactCard.heading(nil), "About this sender")
    }
}
