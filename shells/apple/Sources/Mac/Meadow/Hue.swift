import Foundation

// A name's hue (#44), the mockup's `hue()`, which the avatars and the rows must agree on with the page.

enum MeadowHue {
    /// A name's hue in degrees, 0 to 359: seven, then for each character the hue times 31 and the character's
    /// first UTF-16 unit, kept under 360. JavaScript's `for…of` goes by code point and `charCodeAt(0)` reads the
    /// first unit, so a character past the Basic Multilingual Plane counts once, by its high surrogate.
    static func of(_ name: String) -> Double {
        var hue = 7
        for scalar in name.unicodeScalars {
            hue = (hue * 31 + Int(String(scalar).utf16.first ?? 0)) % 360
        }
        return Double(hue)
    }
}
