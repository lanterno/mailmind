import SwiftUI

// Meadow's colours (#44), as the mockup sets them for Day, Dusk and Night, with the page's account colours and its
// high contrast (web/assets/styles/tokens.css), which Settings' switch or macOS's Increase Contrast turns on. Written
// once from the mockup by a script; change a value here and in the page's tokens together.

/// The light the window is in: the `theme` preference the page and the app share.
enum MeadowLight: String, CaseIterable, Sendable {
    case day, dusk, night

    init(_ theme: API.Theme) { self = MeadowLight(rawValue: theme.rawValue) ?? .day }

    var isDark: Bool { self == .night }
}

/// One of a panel's shadows as the CSS draws it: an offset, a blur, a spread, a colour, and whether it is inset.
struct MeadowShadow: Sendable, Hashable {
    let x: CGFloat, y: CGFloat, blur: CGFloat, spread: CGFloat
    let color: Color
    let inset: Bool
}

/// Every colour Meadow paints with, for one light.
struct MeadowPalette: Sendable {
    /// The sky, top to bottom: the window's edges and the painted scene.
    let sky1: Color
    let sky2: Color
    let sky3: Color
    /// The clouds' gradient, light to shade.
    let cloud1: Color
    let cloud2: Color
    let cloud3: Color
    /// The sun or the moon, and its glow.
    let disc: Color
    let discGlow: Color
    /// The scene's mountains, hills, meadow, tree, cottage and path.
    let mtn: Color
    let hillFar: Color
    let hillMid: Color
    let hillNear: Color
    let hillLit: Color
    let meadow: Color
    let leaf1: Color
    let leaf2: Color
    let leaf3: Color
    let trunk: Color
    let roof: Color
    let wall: Color
    let window: Color
    let path: Color
    /// Paper: the panels, a step darker, a step darker again, and raised (cards, fields, buttons).
    let paper: Color
    let paper2: Color
    let paper3: Color
    let paperRaised: Color
    /// Hairlines, and the stronger one under buttons and fields.
    let line: Color
    let line2: Color
    /// Text: ink, softer, and the quietest that still reads (AA on every paper).
    let ink: Color
    let ink2: Color
    let ink3: Color
    /// Aizome indigo: every action, selection and focus ring.
    let primary: Color
    let primaryHi: Color
    let onPrimary: Color
    let primaryWash: Color
    let primaryInk: Color
    /// Sun gold: only what needs the person, as a fill, as text, and as a wash.
    let sun: Color
    let sunInk: Color
    let sunWash: Color
    /// Moss: only ever the AI's voice, and its wash.
    let moss: Color
    let mossWash: Color
    /// Rust: only Delete and errors.
    let rust: Color
    let rustWash: Color
    /// The focus ring.
    let focus: Color
    /// Behind a dialog.
    let scrim: Color
    /// The accounts' colours, in the order the config lists them; past the sixth they repeat.
    let accounts: [Color]
    /// How much of the stars, the evening glow (fireflies, a lit window) and the sun the scene shows, 0 to 1.
    let stars: Double, glow: Double, sunny: Double
    /// The paper's grain: a fine noise in this colour over every panel.
    let grain: Color
    /// A panel's shadow, and a raised card's or a dialog's.
    let shadow: [MeadowShadow], shadowPop: [MeadowShadow]
    /// Whether paper is never seen through: high contrast's panels, fields and pills stand opaque over the sky, so
    /// their text reads against the paper alone (`seen(_:)`).
    var opaque = false

    /// The colour of the account at `index` in the config's order.
    func account(_ index: Int) -> Color { accounts[((index % accounts.count) + accounts.count) % accounts.count] }

    /// How much of a panel's paper shows, where the mockup lets the sky through it: all of it in high contrast.
    func seen(_ opacity: Double) -> Double { opaque ? 1 : opacity }

    static func of(_ light: MeadowLight, highContrast: Bool = false) -> MeadowPalette {
        switch (light, highContrast) {
        case (.day, false): return .day
        case (.day, true): return .dayHighContrast
        case (.dusk, false): return .dusk
        case (.dusk, true): return .duskHighContrast
        case (.night, false): return .night
        case (.night, true): return .nightHighContrast
        }
    }

    static let day = MeadowPalette(
        sky1: Color(meadow: 0x97BFDD),
        sky2: Color(meadow: 0xC7DDEB),
        sky3: Color(meadow: 0xEDE8DA),
        cloud1: Color(meadow: 0xFFFFFF),
        cloud2: Color(meadow: 0xF4F3EE),
        cloud3: Color(meadow: 0xAFC2D8),
        disc: Color(meadow: 0xFFF6DC),
        discGlow: Color(meadow: 0xFFF3CE, opacity: 0.85),
        mtn: Color(meadow: 0xA3B9CB),
        hillFar: Color(meadow: 0x86AA8C),
        hillMid: Color(meadow: 0x62915F),
        hillNear: Color(meadow: 0x4D7E4B),
        hillLit: Color(meadow: 0x93BC6E),
        meadow: Color(meadow: 0x6E9C55),
        leaf1: Color(meadow: 0xA6CB72),
        leaf2: Color(meadow: 0x5E9147),
        leaf3: Color(meadow: 0x2F5A35),
        trunk: Color(meadow: 0x5C4737),
        roof: Color(meadow: 0xB1523A),
        wall: Color(meadow: 0xF5EBD8),
        window: Color(meadow: 0x5B7789),
        path: Color(meadow: 0xE8DDBF),
        paper: Color(meadow: 0xFCF9F2),
        paper2: Color(meadow: 0xF6F1E6),
        paper3: Color(meadow: 0xEEE7D8),
        paperRaised: Color(meadow: 0xFFFDF8),
        line: Color(meadow: 0xE8DFCE),
        line2: Color(meadow: 0xD5C9B1),
        ink: Color(meadow: 0x24221E),
        ink2: Color(meadow: 0x47423A),
        ink3: Color(meadow: 0x655E50),
        primary: Color(meadow: 0x2D4B70),
        primaryHi: Color(meadow: 0x223A58),
        onPrimary: Color(meadow: 0xFFFFFF),
        primaryWash: Color(meadow: 0xE2E9F2),
        primaryInk: Color(meadow: 0x2D4B70),
        sun: Color(meadow: 0xE3A136),
        sunInk: Color(meadow: 0x7E4F06),
        sunWash: Color(meadow: 0xFAEDD2),
        moss: Color(meadow: 0x37633D),
        mossWash: Color(meadow: 0xE7EFDF),
        rust: Color(meadow: 0xA3412A),
        rustWash: Color(meadow: 0xF7E3DA),
        focus: Color(meadow: 0x2D4B70),
        scrim: Color(meadow: 0x283852, opacity: 0.3),
        accounts: [
            Color(meadow: 0x3D6596), Color(meadow: 0x5C8F4D), Color(meadow: 0xC47A28),
            Color(meadow: 0x8A4F86), Color(meadow: 0x2F7A78), Color(meadow: 0xA8454F),
        ],
        stars: 0, glow: 0, sunny: 1,
        grain: Color(red: 0.36, green: 0.3, blue: 0.2, opacity: 0.07),
        shadow: [
            .init(x: 0, y: 1, blur: 0, spread: 0,
                  color: Color(meadow: 0xFFFFFF, opacity: 0.75), inset: true),
            .init(x: 0, y: 0, blur: 0, spread: 1,
                  color: Color(meadow: 0x685634, opacity: 0.1), inset: false),
            .init(x: 0, y: 14, blur: 34, spread: -22,
                  color: Color(meadow: 0x264068, opacity: 0.55), inset: false),
        ],
        shadowPop: [
            .init(x: 0, y: 0, blur: 0, spread: 1,
                  color: Color(meadow: 0x685634, opacity: 0.12), inset: false),
            .init(x: 0, y: 28, blur: 60, spread: -28,
                  color: Color(meadow: 0x1E345A, opacity: 0.55), inset: false),
        ])

    static let dayHighContrast = MeadowPalette(
        sky1: Color(meadow: 0x97BFDD),
        sky2: Color(meadow: 0xC7DDEB),
        sky3: Color(meadow: 0xEDE8DA),
        cloud1: Color(meadow: 0xFFFFFF),
        cloud2: Color(meadow: 0xF4F3EE),
        cloud3: Color(meadow: 0xAFC2D8),
        disc: Color(meadow: 0xFFF6DC),
        discGlow: Color(meadow: 0xFFF3CE, opacity: 0.85),
        mtn: Color(meadow: 0xA3B9CB),
        hillFar: Color(meadow: 0x86AA8C),
        hillMid: Color(meadow: 0x62915F),
        hillNear: Color(meadow: 0x4D7E4B),
        hillLit: Color(meadow: 0x93BC6E),
        meadow: Color(meadow: 0x6E9C55),
        leaf1: Color(meadow: 0xA6CB72),
        leaf2: Color(meadow: 0x5E9147),
        leaf3: Color(meadow: 0x2F5A35),
        trunk: Color(meadow: 0x5C4737),
        roof: Color(meadow: 0xB1523A),
        wall: Color(meadow: 0xF5EBD8),
        window: Color(meadow: 0x5B7789),
        path: Color(meadow: 0xE8DDBF),
        paper: Color(meadow: 0xFFFFFF),
        paper2: Color(meadow: 0xF7F2E8),
        paper3: Color(meadow: 0xEDE7D8),
        paperRaised: Color(meadow: 0xFFFFFF),
        line: Color(meadow: 0x8C8270),
        line2: Color(meadow: 0x6E6554),
        ink: Color(meadow: 0x000000),
        ink2: Color(meadow: 0x201C16),
        ink3: Color(meadow: 0x332E26),
        primary: Color(meadow: 0x1E3A5F),
        primaryHi: Color(meadow: 0x152C49),
        onPrimary: Color(meadow: 0xFFFFFF),
        primaryWash: Color(meadow: 0xDCE6F2),
        primaryInk: Color(meadow: 0x1E3A5F),
        sun: Color(meadow: 0xE3A136),
        sunInk: Color(meadow: 0x5A3703),
        sunWash: Color(meadow: 0xFAEDD2),
        moss: Color(meadow: 0x1F4726),
        mossWash: Color(meadow: 0xE7EFDF),
        rust: Color(meadow: 0x7C2614),
        rustWash: Color(meadow: 0xF7E3DA),
        focus: Color(meadow: 0x000000),
        scrim: Color(meadow: 0x283852, opacity: 0.3),
        accounts: [
            Color(meadow: 0x3D6596), Color(meadow: 0x5C8F4D), Color(meadow: 0xC47A28),
            Color(meadow: 0x8A4F86), Color(meadow: 0x2F7A78), Color(meadow: 0xA8454F),
        ],
        stars: 0, glow: 0, sunny: 1,
        grain: Color(red: 0.36, green: 0.3, blue: 0.2, opacity: 0.07),
        shadow: [
            .init(x: 0, y: 1, blur: 0, spread: 0,
                  color: Color(meadow: 0xFFFFFF, opacity: 0.75), inset: true),
            .init(x: 0, y: 0, blur: 0, spread: 1,
                  color: Color(meadow: 0x685634, opacity: 0.1), inset: false),
            .init(x: 0, y: 14, blur: 34, spread: -22,
                  color: Color(meadow: 0x264068, opacity: 0.55), inset: false),
        ],
        shadowPop: [
            .init(x: 0, y: 0, blur: 0, spread: 1,
                  color: Color(meadow: 0x685634, opacity: 0.12), inset: false),
            .init(x: 0, y: 28, blur: 60, spread: -28,
                  color: Color(meadow: 0x1E345A, opacity: 0.55), inset: false),
        ],
        opaque: true)

    static let dusk = MeadowPalette(
        sky1: Color(meadow: 0xE59A6E),
        sky2: Color(meadow: 0xF2C496),
        sky3: Color(meadow: 0xF3E2C8),
        cloud1: Color(meadow: 0xFFE8CF),
        cloud2: Color(meadow: 0xF9CFAE),
        cloud3: Color(meadow: 0xB98A99),
        disc: Color(meadow: 0xFFE2A6),
        discGlow: Color(meadow: 0xFFCE8C, opacity: 0.9),
        mtn: Color(meadow: 0xA88C9E),
        hillFar: Color(meadow: 0x8D8B76),
        hillMid: Color(meadow: 0x6E7C52),
        hillNear: Color(meadow: 0x56673F),
        hillLit: Color(meadow: 0xB3A05C),
        meadow: Color(meadow: 0x7A8448),
        leaf1: Color(meadow: 0xC9B562),
        leaf2: Color(meadow: 0x6F7E3F),
        leaf3: Color(meadow: 0x3B4527),
        trunk: Color(meadow: 0x4E3A2D),
        roof: Color(meadow: 0x9E4532),
        wall: Color(meadow: 0xF3DFC4),
        window: Color(meadow: 0xF7C66C),
        path: Color(meadow: 0xEED3A8),
        paper: Color(meadow: 0xFCF7EE),
        paper2: Color(meadow: 0xF7EFE2),
        paper3: Color(meadow: 0xF0E5D3),
        paperRaised: Color(meadow: 0xFFFCF6),
        line: Color(meadow: 0xEBDDC8),
        line2: Color(meadow: 0xDAC7AA),
        ink: Color(meadow: 0x27211C),
        ink2: Color(meadow: 0x4A4036),
        ink3: Color(meadow: 0x685B4C),
        primary: Color(meadow: 0x3A4670),
        primaryHi: Color(meadow: 0x2C3759),
        onPrimary: Color(meadow: 0xFFFFFF),
        primaryWash: Color(meadow: 0xE8E6F0),
        primaryInk: Color(meadow: 0x3A4670),
        sun: Color(meadow: 0xE39A3A),
        sunInk: Color(meadow: 0x7C4805),
        sunWash: Color(meadow: 0xFBE9CF),
        moss: Color(meadow: 0x45612F),
        mossWash: Color(meadow: 0xEDF0DC),
        rust: Color(meadow: 0xA13D27),
        rustWash: Color(meadow: 0xF8E1D6),
        focus: Color(meadow: 0x3A4670),
        scrim: Color(meadow: 0x502E28, opacity: 0.28),
        accounts: [
            Color(meadow: 0x4A5E99), Color(meadow: 0x6B8A3E), Color(meadow: 0xC2702A),
            Color(meadow: 0x84497F), Color(meadow: 0x2F7370), Color(meadow: 0xA3424C),
        ],
        stars: 0, glow: 0.75, sunny: 1,
        grain: Color(red: 0.4, green: 0.28, blue: 0.18, opacity: 0.07),
        shadow: [
            .init(x: 0, y: 1, blur: 0, spread: 0,
                  color: Color(meadow: 0xFFFFFF, opacity: 0.7), inset: true),
            .init(x: 0, y: 0, blur: 0, spread: 1,
                  color: Color(meadow: 0x785028, opacity: 0.11), inset: false),
            .init(x: 0, y: 14, blur: 34, spread: -22,
                  color: Color(meadow: 0x783C32, opacity: 0.5), inset: false),
        ],
        shadowPop: [
            .init(x: 0, y: 0, blur: 0, spread: 1,
                  color: Color(meadow: 0x785028, opacity: 0.13), inset: false),
            .init(x: 0, y: 28, blur: 60, spread: -28,
                  color: Color(meadow: 0x6E3228, opacity: 0.5), inset: false),
        ])

    static let duskHighContrast = MeadowPalette(
        sky1: Color(meadow: 0xE59A6E),
        sky2: Color(meadow: 0xF2C496),
        sky3: Color(meadow: 0xF3E2C8),
        cloud1: Color(meadow: 0xFFE8CF),
        cloud2: Color(meadow: 0xF9CFAE),
        cloud3: Color(meadow: 0xB98A99),
        disc: Color(meadow: 0xFFE2A6),
        discGlow: Color(meadow: 0xFFCE8C, opacity: 0.9),
        mtn: Color(meadow: 0xA88C9E),
        hillFar: Color(meadow: 0x8D8B76),
        hillMid: Color(meadow: 0x6E7C52),
        hillNear: Color(meadow: 0x56673F),
        hillLit: Color(meadow: 0xB3A05C),
        meadow: Color(meadow: 0x7A8448),
        leaf1: Color(meadow: 0xC9B562),
        leaf2: Color(meadow: 0x6F7E3F),
        leaf3: Color(meadow: 0x3B4527),
        trunk: Color(meadow: 0x4E3A2D),
        roof: Color(meadow: 0x9E4532),
        wall: Color(meadow: 0xF3DFC4),
        window: Color(meadow: 0xF7C66C),
        path: Color(meadow: 0xEED3A8),
        paper: Color(meadow: 0xFFFDF9),
        paper2: Color(meadow: 0xF7EFE2),
        paper3: Color(meadow: 0xEDE2CF),
        paperRaised: Color(meadow: 0xFFFFFF),
        line: Color(meadow: 0x8E7F66),
        line2: Color(meadow: 0x6F614B),
        ink: Color(meadow: 0x000000),
        ink2: Color(meadow: 0x231C15),
        ink3: Color(meadow: 0x362D23),
        primary: Color(meadow: 0x28325A),
        primaryHi: Color(meadow: 0x1C2444),
        onPrimary: Color(meadow: 0xFFFFFF),
        primaryWash: Color(meadow: 0xE1DEEC),
        primaryInk: Color(meadow: 0x28325A),
        sun: Color(meadow: 0xE39A3A),
        sunInk: Color(meadow: 0x583102),
        sunWash: Color(meadow: 0xFBE9CF),
        moss: Color(meadow: 0x2F4720),
        mossWash: Color(meadow: 0xEDF0DC),
        rust: Color(meadow: 0x7A2614),
        rustWash: Color(meadow: 0xF8E1D6),
        focus: Color(meadow: 0x000000),
        scrim: Color(meadow: 0x502E28, opacity: 0.28),
        accounts: [
            Color(meadow: 0x4A5E99), Color(meadow: 0x6B8A3E), Color(meadow: 0xC2702A),
            Color(meadow: 0x84497F), Color(meadow: 0x2F7370), Color(meadow: 0xA3424C),
        ],
        stars: 0, glow: 0.75, sunny: 1,
        grain: Color(red: 0.4, green: 0.28, blue: 0.18, opacity: 0.07),
        shadow: [
            .init(x: 0, y: 1, blur: 0, spread: 0,
                  color: Color(meadow: 0xFFFFFF, opacity: 0.7), inset: true),
            .init(x: 0, y: 0, blur: 0, spread: 1,
                  color: Color(meadow: 0x785028, opacity: 0.11), inset: false),
            .init(x: 0, y: 14, blur: 34, spread: -22,
                  color: Color(meadow: 0x783C32, opacity: 0.5), inset: false),
        ],
        shadowPop: [
            .init(x: 0, y: 0, blur: 0, spread: 1,
                  color: Color(meadow: 0x785028, opacity: 0.13), inset: false),
            .init(x: 0, y: 28, blur: 60, spread: -28,
                  color: Color(meadow: 0x6E3228, opacity: 0.5), inset: false),
        ],
        opaque: true)

    static let night = MeadowPalette(
        sky1: Color(meadow: 0x08122A),
        sky2: Color(meadow: 0x122345),
        sky3: Color(meadow: 0x26365A),
        cloud1: Color(meadow: 0x4C5D84),
        cloud2: Color(meadow: 0x33446A),
        cloud3: Color(meadow: 0x18223F),
        disc: Color(meadow: 0xF1ECD4),
        discGlow: Color(meadow: 0xE1E1C8, opacity: 0.35),
        mtn: Color(meadow: 0x22325A),
        hillFar: Color(meadow: 0x1C3242),
        hillMid: Color(meadow: 0x172C2C),
        hillNear: Color(meadow: 0x122420),
        hillLit: Color(meadow: 0x2B4636),
        meadow: Color(meadow: 0x1D3A2B),
        leaf1: Color(meadow: 0x3F6448),
        leaf2: Color(meadow: 0x23412E),
        leaf3: Color(meadow: 0x12241A),
        trunk: Color(meadow: 0x2A221C),
        roof: Color(meadow: 0x59302A),
        wall: Color(meadow: 0x4A4A55),
        window: Color(meadow: 0xFFC468),
        path: Color(meadow: 0x3A4A52),
        paper: Color(meadow: 0x16213A),
        paper2: Color(meadow: 0x121B31),
        paper3: Color(meadow: 0x1F2C49),
        paperRaised: Color(meadow: 0x1B2743),
        line: Color(meadow: 0x26344F),
        line2: Color(meadow: 0x36466B),
        ink: Color(meadow: 0xEDE7D7),
        ink2: Color(meadow: 0xCBC4B3),
        ink3: Color(meadow: 0xA7A193),
        primary: Color(meadow: 0xA9C2EC),
        primaryHi: Color(meadow: 0xC5D7F6),
        onPrimary: Color(meadow: 0x0D1930),
        primaryWash: Color(meadow: 0x24375C),
        primaryInk: Color(meadow: 0xBDD0F2),
        sun: Color(meadow: 0xF2B65A),
        sunInk: Color(meadow: 0xF6CC86),
        sunWash: Color(meadow: 0x3A3121),
        moss: Color(meadow: 0xAED6A2),
        mossWash: Color(meadow: 0x1D3229),
        rust: Color(meadow: 0xF0957A),
        rustWash: Color(meadow: 0x3C2523),
        focus: Color(meadow: 0xC5D7F6),
        scrim: Color(meadow: 0x040814, opacity: 0.55),
        accounts: [
            Color(meadow: 0x8FB0E3), Color(meadow: 0x9FCB8C), Color(meadow: 0xE9AE62),
            Color(meadow: 0xD3A2D0), Color(meadow: 0x86CBC6), Color(meadow: 0xE89FA6),
        ],
        stars: 1, glow: 1, sunny: 0,
        grain: Color(red: 0.8, green: 0.85, blue: 1, opacity: 0.035),
        shadow: [
            .init(x: 0, y: 1, blur: 0, spread: 0,
                  color: Color(meadow: 0xAABEF0, opacity: 0.06), inset: true),
            .init(x: 0, y: 0, blur: 0, spread: 1,
                  color: Color(meadow: 0x96AFE6, opacity: 0.1), inset: false),
            .init(x: 0, y: 18, blur: 40, spread: -22,
                  color: Color(meadow: 0x000000, opacity: 0.85), inset: false),
        ],
        shadowPop: [
            .init(x: 0, y: 0, blur: 0, spread: 1,
                  color: Color(meadow: 0x96AFE6, opacity: 0.14), inset: false),
            .init(x: 0, y: 30, blur: 70, spread: -28,
                  color: Color(meadow: 0x000000, opacity: 0.9), inset: false),
        ])

    static let nightHighContrast = MeadowPalette(
        sky1: Color(meadow: 0x08122A),
        sky2: Color(meadow: 0x122345),
        sky3: Color(meadow: 0x26365A),
        cloud1: Color(meadow: 0x4C5D84),
        cloud2: Color(meadow: 0x33446A),
        cloud3: Color(meadow: 0x18223F),
        disc: Color(meadow: 0xF1ECD4),
        discGlow: Color(meadow: 0xE1E1C8, opacity: 0.35),
        mtn: Color(meadow: 0x22325A),
        hillFar: Color(meadow: 0x1C3242),
        hillMid: Color(meadow: 0x172C2C),
        hillNear: Color(meadow: 0x122420),
        hillLit: Color(meadow: 0x2B4636),
        meadow: Color(meadow: 0x1D3A2B),
        leaf1: Color(meadow: 0x3F6448),
        leaf2: Color(meadow: 0x23412E),
        leaf3: Color(meadow: 0x12241A),
        trunk: Color(meadow: 0x2A221C),
        roof: Color(meadow: 0x59302A),
        wall: Color(meadow: 0x4A4A55),
        window: Color(meadow: 0xFFC468),
        path: Color(meadow: 0x3A4A52),
        paper: Color(meadow: 0x101A30),
        paper2: Color(meadow: 0x0C1428),
        paper3: Color(meadow: 0x1A2640),
        paperRaised: Color(meadow: 0x141E36),
        line: Color(meadow: 0x55668C),
        line2: Color(meadow: 0x7689B4),
        ink: Color(meadow: 0xFFFFFF),
        ink2: Color(meadow: 0xF2EDDE),
        ink3: Color(meadow: 0xE0DACA),
        primary: Color(meadow: 0xC5D7F6),
        primaryHi: Color(meadow: 0xDCE7FA),
        onPrimary: Color(meadow: 0x0D1930),
        primaryWash: Color(meadow: 0x20325A),
        primaryInk: Color(meadow: 0xD3E1FA),
        sun: Color(meadow: 0xF2B65A),
        sunInk: Color(meadow: 0xFFD896),
        sunWash: Color(meadow: 0x3A3121),
        moss: Color(meadow: 0xC8EBBC),
        mossWash: Color(meadow: 0x1D3229),
        rust: Color(meadow: 0xFFB39C),
        rustWash: Color(meadow: 0x3C2523),
        focus: Color(meadow: 0xFFFFFF),
        scrim: Color(meadow: 0x040814, opacity: 0.55),
        accounts: [
            Color(meadow: 0x8FB0E3), Color(meadow: 0x9FCB8C), Color(meadow: 0xE9AE62),
            Color(meadow: 0xD3A2D0), Color(meadow: 0x86CBC6), Color(meadow: 0xE89FA6),
        ],
        stars: 1, glow: 1, sunny: 0,
        grain: Color(red: 0.8, green: 0.85, blue: 1, opacity: 0.035),
        shadow: [
            .init(x: 0, y: 1, blur: 0, spread: 0,
                  color: Color(meadow: 0xAABEF0, opacity: 0.06), inset: true),
            .init(x: 0, y: 0, blur: 0, spread: 1,
                  color: Color(meadow: 0x96AFE6, opacity: 0.1), inset: false),
            .init(x: 0, y: 18, blur: 40, spread: -22,
                  color: Color(meadow: 0x000000, opacity: 0.85), inset: false),
        ],
        shadowPop: [
            .init(x: 0, y: 0, blur: 0, spread: 1,
                  color: Color(meadow: 0x96AFE6, opacity: 0.14), inset: false),
            .init(x: 0, y: 30, blur: 70, spread: -28,
                  color: Color(meadow: 0x000000, opacity: 0.9), inset: false),
        ],
        opaque: true)
}

extension Color {
    /// A colour from its hex, as the mockup writes it.
    init(meadow hex: UInt32, opacity: Double = 1) {
        self.init(.sRGB, red: Double((hex >> 16) & 0xFF) / 255, green: Double((hex >> 8) & 0xFF) / 255,
                  blue: Double(hex & 0xFF) / 255, opacity: opacity)
    }
}
