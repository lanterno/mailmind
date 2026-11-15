import SwiftUI

// A person's avatar (#44), as the mockup draws one: their initials on a circle whose hue comes from their name
// (`MeadowHue`), so the same name has the same colour everywhere, and the dot of the account the mail came to.

/// A person's avatar (`.av`): 34 across by default, 24 small, 42 large and 56 for the gate's visitor, with the dot of
/// the account the mail came to at its foot.
struct MeadowAvatar: View {
    enum Size: CGFloat, CaseIterable {
        case small = 24
        case regular = 34
        case large = 42
        case huge = 56

        /// The initials' size at each.
        var font: CGFloat {
            switch self {
            case .small: 9.5
            case .regular: 12
            case .large: 14
            case .huge: 18
            }
        }
    }

    let name: String
    var size: Size = .regular
    /// The account's colour, for its dot.
    var account: Color?
    /// On a chosen row, whose wash the dot's ring takes.
    var chosen = false
    @Environment(\.meadow) private var meadow
    @Environment(\.meadowLight) private var light

    var body: some View {
        let colours = Self.colours(MeadowHue.of(name), in: light)
        Circle()
            .fill(colours.fill)
            .frame(width: size.rawValue, height: size.rawValue)
            .overlay {
                Text(verbatim: Self.initials(name))
                    .font(.meadow(.ui, size.font, .heavy))
                    .tracking(size.font * 0.02)
                    .foregroundStyle(colours.ink)
            }
            .overlay(alignment: .bottomTrailing) {
                if let account {
                    Circle()
                        .fill(account)
                        .overlay { Circle().strokeBorder(chosen ? meadow.primaryWash : meadow.paper, lineWidth: 2.5) }
                        .frame(width: 12, height: 12)
                        .offset(x: 2, y: 2)
                }
            }
            .accessibilityHidden(true)
    }

    /// The mockup's `initials()`: the first letters of a name's first two words, counting only the ASCII letters and
    /// spaces in it.
    static func initials(_ name: String) -> String {
        let kept = name.unicodeScalars.filter { $0.isASCII && ($0 == " " || CharacterSet.letters.contains($0)) }
        let words = String(String.UnicodeScalarView(kept)).split(separator: " ")
        return String(words.prefix(2).compactMap(\.first)).uppercased()
    }

    /// The circle and the initials in a light: pale and deep by day and dusk, dim and light at night.
    static func colours(_ hue: Double, in light: MeadowLight) -> (fill: Color, ink: Color) {
        light == .night ? (hsl(hue, 0.24, 0.30), hsl(hue, 0.50, 0.88)) : (hsl(hue, 0.40, 0.86), hsl(hue, 0.40, 0.25))
    }

    /// `hsl(h s% l%)` as a colour.
    static func hsl(_ hue: Double, _ saturation: Double, _ lightness: Double) -> Color {
        let (r, g, b) = rgb(hue, saturation, lightness)
        return Color(.sRGB, red: r, green: g, blue: b, opacity: 1)
    }

    /// The sRGB channels of `hsl(h s l)`, saturation and lightness from 0 to 1, as CSS converts them.
    static func rgb(_ hue: Double, _ saturation: Double, _ lightness: Double) -> (Double, Double, Double) {
        let h = (hue.truncatingRemainder(dividingBy: 360) + 360).truncatingRemainder(dividingBy: 360) / 60
        let chroma = (1 - abs(2 * lightness - 1)) * saturation
        let x = chroma * (1 - abs(h.truncatingRemainder(dividingBy: 2) - 1))
        let (r, g, b): (Double, Double, Double) = switch h {
        case ..<1: (chroma, x, 0)
        case ..<2: (x, chroma, 0)
        case ..<3: (0, chroma, x)
        case ..<4: (0, x, chroma)
        case ..<5: (x, 0, chroma)
        default: (chroma, 0, x)
        }
        let m = lightness - chroma / 2
        return (r + m, g + m, b + m)
    }
}

/// MailMind's mark (the brand's five paths): the sun, and a paper plane in indigo, its far wing lighter and its fold
/// darker.
struct MeadowBrandMark: View {
    var size: CGFloat = 28
    @Environment(\.meadow) private var meadow

    var body: some View {
        Canvas { context, canvas in
            let s = canvas.width / 32
            func shape(_ points: [(CGFloat, CGFloat)]) -> Path {
                var path = Path()
                path.addLines(points.map { CGPoint(x: $0.0 * s, y: $0.1 * s) })
                path.closeSubpath()
                return path
            }
            let wing = meadow.primary.meadowMix(0.62, with: meadow.paper, in: context.environment)
            let fold = meadow.primary.meadowMix(0.82, with: .black, in: context.environment)
            context.fill(Path(ellipseIn: CGRect(x: 21.3 * s, y: 4.3 * s, width: 6.4 * s, height: 6.4 * s)),
                         with: .color(meadow.sun))
            context.fill(shape([(3, 18.5), (17.5, 9.5), (14, 19)]), with: .color(meadow.primary))
            context.fill(shape([(3, 18.5), (14, 19), (28.5, 11), (19, 24.5)]), with: .color(wing))
            context.fill(shape([(14, 19), (19, 24.5), (13.8, 26.7)]), with: .color(fold))
            context.fill(shape([(28.5, 11), (31, 9.6), (28, 13.2)]), with: .color(meadow.primary))
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}
