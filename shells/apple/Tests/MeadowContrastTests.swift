import AppKit
import SwiftUI
import XCTest

/// Meadow's contrast: every light's text reads at WCAG AA on its panels, as they stand over the sky, and macOS's
/// Increase Contrast puts every light in high contrast, with firmer lines and opaque panels.
@MainActor
final class MeadowContrastTests: XCTestCase {
    /// The see-through panels the mockup lays over the sky: the status pill's, the summary's actions', the rail's and
    /// the search field's.
    private let seeThrough: [Double] = [0.62, 0.78, 0.86, 0.9]

    func testEveryLightsTextIsAAOnItsPanels() {
        for light in MeadowLight.allCases {
            for high in [false, true] {
                let meadow = MeadowPalette.of(light, highContrast: high)
                let name = "\(light)\(high ? " in high contrast" : "")"
                let texts: [(String, Color)] = [("ink", meadow.ink), ("ink2", meadow.ink2), ("ink3", meadow.ink3),
                                                ("primaryInk", meadow.primaryInk), ("sunInk", meadow.sunInk),
                                                ("moss", meadow.moss), ("rust", meadow.rust)]
                let papers: [(String, Color)] = [("paper", meadow.paper), ("paper2", meadow.paper2),
                                                 ("paper3", meadow.paper3), ("paperRaised", meadow.paperRaised)]
                for (text, ink) in texts {
                    for (ground, paper) in papers {
                        XCTAssertGreaterThanOrEqual(Self.contrast(ink, paper), 4.5, "\(text) on \(ground), \(name)")
                    }
                }
                // Each voice on its own wash: a chip, the summary, a refusal.
                for (text, ink, wash) in [("primaryInk", meadow.primaryInk, meadow.primaryWash),
                                          ("sunInk", meadow.sunInk, meadow.sunWash),
                                          ("moss", meadow.moss, meadow.mossWash), ("rust", meadow.rust, meadow.rustWash)] {
                    XCTAssertGreaterThanOrEqual(Self.contrast(ink, wash), 4.5, "\(text) on its wash, \(name)")
                }
                // A panel the sky shows through, at its darkest and lightest, as the window composites it.
                for alpha in seeThrough {
                    for sky in [meadow.sky1, meadow.sky2, meadow.sky3] {
                        for paper in [meadow.paper, meadow.paperRaised] {
                            let ground = Self.over(paper, alpha: meadow.seen(alpha), sky)
                            for (text, ink) in texts.prefix(3) {
                                XCTAssertGreaterThanOrEqual(Self.contrast(ink, ground), 4.5,
                                                            "\(text) on a \(alpha) panel over the sky, \(name)")
                            }
                        }
                    }
                }
            }
        }
    }

    func testHighContrastsPanelsAreOpaqueAndItsLinesFirm() {
        for light in MeadowLight.allCases {
            let plain = MeadowPalette.of(light), high = MeadowPalette.of(light, highContrast: true)
            XCTAssertFalse(plain.opaque, "\(light) lets the sky through its rail, as the mockup does")
            XCTAssertEqual(plain.seen(0.86), 0.86)
            XCTAssertTrue(high.opaque, "\(light) in high contrast stands on solid paper")
            XCTAssertEqual(high.seen(0.86), 1)
            // WCAG's 3:1 for what isn't text: the hairline on its panel, and the line under a button or field on
            // every paper.
            XCTAssertGreaterThanOrEqual(Self.contrast(high.line, high.paper), 3, "the hairline, \(light)")
            for paper in [high.paper, high.paper2, high.paper3, high.paperRaised] {
                XCTAssertGreaterThanOrEqual(Self.contrast(high.line2, paper), 3, "a field's line, \(light)")
            }
            XCTAssertGreaterThan(Self.contrast(high.line, high.paper), Self.contrast(plain.line, plain.paper),
                                 "firmer than \(light)’s own")
            XCTAssertGreaterThan(Self.contrast(high.ink3, high.paper), Self.contrast(plain.ink3, plain.paper),
                                 "and darker ink")
        }
    }

    func testIncreaseContrastPutsEveryLightInHighContrast() throws {
        let status = AppStatus(newMail: NewMailAlerts(notifier: SystemNotifier.Silent(), isActive: { true },
                                                      badge: { _ in }, announce: { _ in }))
        status.hold(preferences: StatusFixtures.preferences(), now: StatusFixtures.at("2026-11-14T15:00:00Z"))
        XCTAssertFalse(status.highContrast, "Settings' switch is off and so is the system's")
        let plain = try Self.panelDrawn(status)
        XCTAssertLessThan(plain.alpha, 0.9, "the sky shows through a panel")

        status.systemIncreasesContrast = true
        XCTAssertTrue(status.highContrast, "macOS's Increase Contrast turns it on, whatever Settings says")
        let high = try Self.panelDrawn(status)
        XCTAssertEqual(high.alpha, 1, accuracy: 0.01, "the panel stands opaque")
        let black = try XCTUnwrap(NSColor(MeadowPalette.dayHighContrast.ink).usingColorSpace(.sRGB))
        XCTAssertEqual(high.ink.redComponent, black.redComponent, accuracy: 0.02, "in high contrast's ink")
        XCTAssertNotEqual(plain.ink.redComponent, high.ink.redComponent, accuracy: 0.02)

        status.systemIncreasesContrast = false
        XCTAssertFalse(status.highContrast)
    }

    /// The pill over the list for mail that came in since ("▾ 2 new"), on the window's material, which the list shows
    /// through: in high contrast it stands on solid paper, as the other panels do, in each light.
    func testTheNewMailPillIsOpaqueInHighContrast() throws {
        for light in MeadowLight.allCases {
            let face = NewMailPillFace(words: "▾ 2 new — open the Inbox", line: "Grace sent the figures.")
            // In its padding, left of the words, clear of the rounded corners and the edge's shadow.
            let pill = try XCTUnwrap(Self.drawn(face, light: light) { CGPoint(x: 10, y: $0.height / 2) })
            // The paper as the same renderer draws it: a picture's colours aren't the palette's own in a dark light.
            let paper = try XCTUnwrap(Self.drawn(PaperProbe(), light: light) { _ in CGPoint(x: 5, y: 5) })
            XCTAssertEqual(pill.alphaComponent, 1, accuracy: 0.01, "\(light): opaque")
            for (drawn, wanted) in [(pill.redComponent, paper.redComponent),
                                    (pill.greenComponent, paper.greenComponent),
                                    (pill.blueComponent, paper.blueComponent)] {
                XCTAssertEqual(drawn, wanted, accuracy: 0.02, "\(light): on the paper, not the material")
            }
        }
    }

    /// One point of `content` drawn in `light`'s high contrast, at the point `at` picks for the picture's size.
    private static func drawn(_ content: some View, light: MeadowLight, at: (CGSize) -> CGPoint) throws -> NSColor? {
        let renderer = ImageRenderer(content: content.meadowLight(light, highContrast: true))
        renderer.scale = 1
        let bitmap = NSBitmapImageRep(cgImage: try XCTUnwrap(renderer.cgImage, "it drew"))
        let point = at(CGSize(width: bitmap.pixelsWide, height: bitmap.pixelsHigh))
        return bitmap.colorAt(x: Int(point.x), y: Int(point.y))?.usingColorSpace(.sRGB)
    }

    private struct PaperProbe: View {
        @Environment(\.meadow) private var meadow

        var body: some View { meadow.paperRaised.frame(width: 10, height: 10) }
    }

    // MARK: Helpers

    /// A panel half seen through, and a square of ink, as a window lit by `status` draws them.
    private static func panelDrawn(_ status: AppStatus) throws -> (alpha: CGFloat, ink: NSColor) {
        let renderer = ImageRenderer(content: Probe().meadowLit(status))
        renderer.scale = 1
        let bitmap = NSBitmapImageRep(cgImage: try XCTUnwrap(renderer.cgImage, "the probe drew"))
        let panel = try XCTUnwrap(bitmap.colorAt(x: 30, y: 30)?.usingColorSpace(.sRGB))
        let ink = try XCTUnwrap(bitmap.colorAt(x: 90, y: 30)?.usingColorSpace(.sRGB))
        return (panel.alphaComponent, ink)
    }

    private struct Probe: View {
        @Environment(\.meadow) private var meadow

        var body: some View {
            HStack(spacing: 0) {
                Color.clear.frame(width: 60, height: 60).meadowPanel(radius: 4, opacity: 0.5)
                meadow.ink.frame(width: 60, height: 60)
            }
        }
    }

    /// WCAG's contrast ratio between two colours.
    static func contrast(_ a: Color, _ b: Color) -> Double {
        let (la, lb) = (luminance(a), luminance(b))
        return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)
    }

    /// WCAG's relative luminance of an sRGB colour.
    static func luminance(_ color: Color) -> Double {
        guard let c = NSColor(color).usingColorSpace(.sRGB) else { return 0 }
        func linear(_ v: CGFloat) -> Double {
            let v = Double(v)
            return v <= 0.03928 ? v / 12.92 : pow((v + 0.055) / 1.055, 2.4)
        }
        return 0.2126 * linear(c.redComponent) + 0.7152 * linear(c.greenComponent) + 0.0722 * linear(c.blueComponent)
    }

    /// `top` at `alpha` over `under`, as the window composites them in sRGB.
    static func over(_ top: Color, alpha: Double, _ under: Color) -> Color {
        guard let t = NSColor(top).usingColorSpace(.sRGB), let u = NSColor(under).usingColorSpace(.sRGB) else {
            return top
        }
        func mix(_ a: CGFloat, _ b: CGFloat) -> Double { alpha * Double(a) + (1 - alpha) * Double(b) }
        return Color(.sRGB, red: mix(t.redComponent, u.redComponent), green: mix(t.greenComponent, u.greenComponent),
                     blue: mix(t.blueComponent, u.blueComponent))
    }
}
