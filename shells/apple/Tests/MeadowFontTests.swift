import AppKit
import CoreText
import SwiftUI
import XCTest

/// Meadow's faces register from the bundle, each family resolves, and each weight asked for is a different weight:
/// Nunito Sans' from its variable font, Shippori Mincho's two, Klee One's one.
final class MeadowFontTests: XCTestCase {
    private let wght = 0x7767_6874   // 'wght', the weight axis

    override func setUp() {
        XCTAssertEqual(MeadowFonts.register(), [], "every face the app carries registers")
    }

    private func weightAxis(_ font: CTFont) -> CGFloat? {
        (CTFontCopyVariation(font) as? [NSNumber: NSNumber])?[NSNumber(value: wght)].map { CGFloat($0.doubleValue) }
    }

    private func width(_ font: NSFont, _ text: String = "Could you sign and send it back by Monday?") -> CGFloat {
        NSAttributedString(string: text, attributes: [.font: font]).size().width
    }

    func testEachFamilyIsRegisteredWithItsFaces() {
        let members = { (family: String) in
            (NSFontManager.shared.availableMembers(ofFontFamily: family) ?? []).compactMap { $0.first as? String }
        }
        XCTAssertTrue(members("Nunito Sans").contains("NunitoSans-Regular"))
        XCTAssertTrue(members("Nunito Sans").contains("NunitoSans-Italic"))
        XCTAssertEqual(Set(members("Shippori Mincho")), ["ShipporiMincho-SemiBold", "ShipporiMincho-Bold"])
        XCTAssertEqual(members("Klee One"), ["KleeOne-SemiBold"])
    }

    func testEachFaceResolvesToItsFamily() {
        XCTAssertEqual(NSFont.meadow(.ui, 14).familyName, "Nunito Sans")
        XCTAssertEqual(NSFont.meadow(.read, 17).familyName, "Nunito Sans")
        XCTAssertEqual(NSFont.meadow(.display, 28, .bold).familyName, "Shippori Mincho")
        XCTAssertEqual(NSFont.meadow(.hand, 18, .semibold).familyName, "Klee One")
        XCTAssertTrue(NSFont.meadow(.mono, 10.5, .bold).isFixedPitch)
        XCTAssertEqual(NSFont.meadow(.ui, 13.5).pointSize, 13.5)
    }

    func testNunitoSansWeightsAreEachTheirOwn() {
        let weights: [(NSFont.Weight, CGFloat)] = [(.regular, 400), (.medium, 500), (.semibold, 600), (.bold, 700),
                                                   (.heavy, 800)]
        var last: CGFloat = 0
        for (weight, css) in weights {
            let font = NSFont.meadow(.ui, 17, weight)
            XCTAssertEqual(weightAxis(font) ?? 400, css, "\(weight.rawValue): \(font.fontName)")
            // A heavier weight sets the same line wider, so it is drawn heavier, not just named so.
            XCTAssertGreaterThan(width(font), last, "\(css)")
            last = width(font)
        }
        XCTAssertEqual(NSFont.meadow(.ui, 17).fontName, "NunitoSans-Regular", "Regular is the font's own default")
    }

    func testNunitoSansItalic() {
        let font = NSFont.meadow(.ui, 15, .semibold, italic: true)
        XCTAssertTrue(font.fontDescriptor.symbolicTraits.contains(.italic), font.fontName)
        XCTAssertTrue(font.fontName.hasPrefix("NunitoSans-Italic"), font.fontName)
        XCTAssertEqual(weightAxis(font), 600)
    }

    func testShipporiMinchoAndKleeOneTakeTheWeightsTheyHave() {
        XCTAssertEqual(NSFont.meadow(.display, 28).fontName, "ShipporiMincho-SemiBold")
        XCTAssertEqual(NSFont.meadow(.display, 28, .semibold).fontName, "ShipporiMincho-SemiBold")
        XCTAssertEqual(NSFont.meadow(.display, 28, .bold).fontName, "ShipporiMincho-Bold")
        XCTAssertEqual(NSFont.meadow(.display, 28, .heavy).fontName, "ShipporiMincho-Bold")
        XCTAssertGreaterThan(width(NSFont.meadow(.display, 28, .bold)), width(NSFont.meadow(.display, 28, .semibold)))
        for weight: NSFont.Weight in [.regular, .semibold, .bold] {
            XCTAssertEqual(NSFont.meadow(.hand, 18, weight).fontName, "KleeOne-SemiBold")
        }
    }

    // A Font resolves to its face only from macOS 26 on. The app runs from macOS 15, and its tests on macOS 26.
    @available(macOS 26, *)
    func testSwiftUIsFontsAreTheSameFaces() {
        let context = EnvironmentValues().fontResolutionContext
        let cases: [(MeadowFace, Font.Weight, NSFont.Weight, Bool)] = [
            (.ui, .regular, .regular, false), (.ui, .medium, .medium, false), (.ui, .semibold, .semibold, false),
            (.ui, .bold, .bold, false), (.ui, .heavy, .heavy, false), (.ui, .bold, .bold, true),
            (.display, .semibold, .semibold, false), (.display, .bold, .bold, false),
            (.display, .regular, .regular, false),
            (.hand, .semibold, .semibold, false), (.hand, .bold, .bold, false),
        ]
        for (face, weight, nsWeight, italic) in cases {
            let resolved = Font.meadow(face, 17, weight, italic: italic).resolve(in: context).ctFont
            let expected = NSFont.meadow(face, 17, nsWeight, italic: italic)
            let said = "\(face) \(weight) italic \(italic)"
            XCTAssertEqual(CTFontCopyPostScriptName(resolved) as String, expected.fontName, said)
            XCTAssertEqual(weightAxis(resolved), weightAxis(expected), said)
            XCTAssertEqual(CTFontGetSymbolicTraits(resolved).contains(.traitItalic), italic, said)
            XCTAssertEqual(CTFontGetSize(resolved), 17, said)
        }
    }

    @available(macOS 26, *)
    @MainActor func testAHalfPointSizeIsSetAtItsSize() {
        // The mockup's sizes are often halves: 12.5 is 12.5, not 13.
        let context = EnvironmentValues().fontResolutionContext
        for size: CGFloat in [10.5, 12.5, 13.5, 15.5] {
            XCTAssertEqual(CTFontGetSize(Font.meadow(.ui, size).resolve(in: context).ctFont), size)
            let text = "Mail & interface · Nunito Sans"
            let drawn = NSHostingView(rootView: Text(text).font(.meadow(.ui, size)).fixedSize()).fittingSize.width
            XCTAssertEqual(drawn, width(NSFont.meadow(.ui, size), text), accuracy: 1, "\(size)")
        }
    }

    func testTextIsDrawnWithoutTheMacsSmoothingAsThePageDrawsIt() {
        // Unless the person turned it on or off for every app, which stays theirs.
        let everyApp = UserDefaults(suiteName: UserDefaults.globalDomain)?.object(forKey: "AppleFontSmoothing")
        XCTAssertEqual(UserDefaults.standard.integer(forKey: "AppleFontSmoothing"), (everyApp as? Int) ?? 0)
    }

    func testLineSpacingMakesCSSsLineHeight() {
        let font = NSFont.meadow(.read, 17)
        let natural = font.ascender - font.descender + font.leading
        XCTAssertEqual(MeadowFace.read.lineSpacing(size: 17, lineHeight: 1.72) + natural, 17 * 1.72, accuracy: 0.001)
        XCTAssertEqual(MeadowFace.ui.lineSpacing(size: 14, lineHeight: 1), 0, "never less than the font's own")
    }

    func testTheFacesCoverLatinAndLatinExtended() {
        func covers(_ font: NSFont, _ text: String) -> Bool {
            let units = Array(text.utf16)
            var glyphs = [CGGlyph](repeating: 0, count: units.count)
            return CTFontGetGlyphsForCharacters(font, units, &glyphs, units.count)
        }
        let latin = "Hana, could you sign? 0123456789 é ü ñ ß ø — “£€”"
        for face in [MeadowFace.ui, .display, .hand] {
            XCTAssertTrue(covers(NSFont.meadow(face, 17), latin), "\(face)")
        }
        // Nunito Sans has the whole of the page's: Latin Extended, Vietnamese and Cyrillic.
        XCTAssertTrue(covers(NSFont.meadow(.ui, 17), "ő Ł č ğ ş ż ệ ơ Ж ї"))
        XCTAssertTrue(covers(NSFont.meadow(.ui, 17, italic: true), "ő Ł č ğ ş ż ệ ơ Ж ї"))
        XCTAssertTrue(covers(NSFont.meadow(.hand, 17), "Ł š ž Ж"))
    }
}
