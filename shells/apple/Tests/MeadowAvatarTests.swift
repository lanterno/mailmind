import XCTest

/// A name's hue and initials (#44), which the avatars and the rows must agree on with the page, and the avatar's
/// colours worked out as CSS's `hsl()` works them.
@MainActor
final class MeadowAvatarTests: XCTestCase {
    func testANamesHueAndInitialsAreTheMockups() {
        // Each worked out by the mockup's own `hue()` and `initials()`.
        let names: [(String, Double, String)] = [
            ("Ren Takeda", 48, "RT"), ("Mira Okafor", 322, "MO"), ("Aoi Nakamura", 356, "AN"),
            ("Yuki Mori", 144, "YM"), ("Zoë Ünal", 350, "ZN"), ("Ada Lovelace", 242, "AL"), ("😀 Smile", 222, "S"),
            ("José María López", 50, "JM"), ("", 7, ""), ("ada@example.test", 167, "A"),
        ]
        for (name, hue, initials) in names {
            XCTAssertEqual(MeadowHue.of(name), hue, name)
            XCTAssertEqual(MeadowAvatar.initials(name), initials, name)
        }
    }

    func testHSLAsCSSConvertsIt() {
        let (r, g, b) = MeadowAvatar.rgb(48, 0.40, 0.86)
        XCTAssertEqual([r, g, b].map { Int(($0 * 255).rounded()) }, [234, 228, 205])
        let (r2, g2, b2) = MeadowAvatar.rgb(322, 0.24, 0.30)
        XCTAssertEqual([r2, g2, b2].map { Int(($0 * 255).rounded()) }, [95, 58, 81])
        let (r3, g3, b3) = MeadowAvatar.rgb(-38, 0.24, 0.30)
        XCTAssertEqual([r3, g3, b3].map { Int(($0 * 255).rounded()) }, [95, 58, 81], "a hue below 0 goes round")
    }
}
