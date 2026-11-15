import CoreText
import SwiftUI
#if os(macOS)
import AppKit
#endif

// Meadow's typefaces (#44), the page's own (web/assets/fonts): Nunito Sans for the interface and the mail, Shippori
// Mincho for headings, Klee One for the brief's voice. The app carries them as one file a face (Fonts/, which
// Fonts/fonts.py makes from the page's) and registers them for its own process before the first window opens.

/// Which of Meadow's faces a piece of text is set in.
enum MeadowFace: Sendable {
    /// Nunito Sans: the interface, the list, the mail, summaries.
    case ui
    /// Shippori Mincho (600 and 700): titles, the subject, the brief's greeting.
    case display
    /// Klee One (600): the brief, in the AI's hand.
    case hand
    /// The reading face: Nunito Sans, or a serif when the person picks one.
    case read
    /// Keycaps and code.
    case mono

    /// The family's name, as Core Text knows it once registered.
    var family: String? {
        switch self {
        case .ui, .read: "Nunito Sans"
        case .display: "Shippori Mincho"
        case .hand: "Klee One"
        case .mono: nil
        }
    }

    /// The nearest weight the face has, as CSS numbers it. Nunito Sans has every weight from 200 to 1000; Shippori
    /// Mincho only 600 and 700, and Klee One only 600, so a weight they lack is never drawn by thickening another.
    func weight(nearest css: Int) -> Int {
        switch self {
        case .ui, .read, .mono: min(max(css, 200), 1000)
        case .display: css >= 700 ? 700 : 600
        case .hand: 600
        }
    }
}

/// The faces the app carries, registered for this process once.
enum MeadowFonts {
    /// The files in the app's resources, as Fonts/fonts.py names them.
    static let files = [
        "NunitoSans-Regular", "NunitoSans-Italic", "ShipporiMincho-SemiBold", "ShipporiMincho-Bold", "KleeOne-SemiBold",
    ]

    /// Registers the faces the first time it is called, and says which files couldn't be: none, in a whole app.
    /// The app calls it as it starts; `Font.meadow` and `NSFont.meadow` call it too, so a view drawn away from the app
    /// (a test, a picture) has the faces as well.
    @discardableResult
    static func register() -> [String] { failed }

    private static let failed: [String] = {
        #if os(macOS)
        // The page draws its text without the Mac's font smoothing (`-webkit-font-smoothing: antialiased`), which
        // thickens every stem: a line of Nunito Sans takes a fifth more ink with it. Off for this app too, as a default
        // only, so a person who set it for every app keeps theirs. Text drawn after this reads it.
        UserDefaults.standard.register(defaults: ["AppleFontSmoothing": 0])
        #endif
        let bundle = Bundle(for: BundleToken.self)
        return files.filter { name in
            guard let url = bundle.url(forResource: name, withExtension: "ttf") else { return true }
            var error: Unmanaged<CFError>?
            if CTFontManagerRegisterFontsForURL(url as CFURL, .process, &error) { return false }
            // Already registered is as good as registered: a test bundle and the app it runs in may both have tried.
            let code = error.map { CFErrorGetCode($0.takeRetainedValue()) }
            return code != CTFontManagerError.alreadyRegistered.rawValue
        }
    }()

    /// The bundle these files are built into, which holds the faces: the app's, or a test bundle's.
    private final class BundleToken {}

    /// CSS's weights, which the mockup sets type in, for SwiftUI's.
    static func css(_ weight: Font.Weight) -> Int {
        switch weight {
        case .ultraLight: 100
        case .thin: 200
        case .light: 300
        case .medium: 500
        case .semibold: 600
        case .bold: 700
        case .heavy: 800
        case .black: 900
        default: 400
        }
    }

    static func weight(css: Int) -> Font.Weight {
        switch css {
        case ..<150: .ultraLight
        case ..<250: .thin
        case ..<350: .light
        case ..<450: .regular
        case ..<550: .medium
        case ..<650: .semibold
        case ..<750: .bold
        case ..<850: .heavy
        default: .black
        }
    }
}

extension Font {
    /// Text in one of Meadow's faces, at a size in points (the mockup's px) and a weight, upright or italic.
    static func meadow(_ face: MeadowFace, _ size: CGFloat, _ weight: Font.Weight = .regular,
                       italic: Bool = false) -> Font {
        MeadowFonts.register()
        guard let family = face.family else {
            let mono = Font.system(size: size, weight: weight, design: .monospaced)
            return italic ? mono.italic() : mono
        }
        // At its size exactly: the mockup's are often a half (12.5, 13.5), and a font relative to the body's size
        // sets them a whole point larger.
        let font = Font.custom(family, fixedSize: size)
            .weight(MeadowFonts.weight(css: face.weight(nearest: MeadowFonts.css(weight))))
        return italic ? font.italic() : font
    }
}

#if os(macOS)
extension MeadowFonts {
    static func css(_ weight: NSFont.Weight) -> Int {
        switch weight {
        case .ultraLight: 100
        case .thin: 200
        case .light: 300
        case .medium: 500
        case .semibold: 600
        case .bold: 700
        case .heavy: 800
        case .black: 900
        default: 400
        }
    }

    static func nsWeight(css: Int) -> NSFont.Weight {
        switch css {
        case ..<150: .ultraLight
        case ..<250: .thin
        case ..<350: .light
        case ..<450: .regular
        case ..<550: .medium
        case ..<650: .semibold
        case ..<750: .bold
        case ..<850: .heavy
        default: .black
        }
    }
}

extension MeadowFace {
    /// What SwiftUI's `lineSpacing` adds between lines of this face so they stand as far apart as CSS's
    /// `line-height` sets them (the mockup's 1.45 for the interface, 1.72 for mail): the line's height less the
    /// font's own.
    func lineSpacing(size: CGFloat, lineHeight: CGFloat, weight: NSFont.Weight = .regular) -> CGFloat {
        let font = NSFont.meadow(self, size, weight)
        return max(0, size * lineHeight - (font.ascender - font.descender + font.leading))
    }
}

extension NSFont {
    /// The same for AppKit and Core Text (the list's rows draw their own text). Nunito Sans' weights are its variable
    /// font's named instances, which Core Text picks by the weight asked for.
    static func meadow(_ face: MeadowFace, _ size: CGFloat, _ weight: NSFont.Weight = .regular,
                       italic: Bool = false) -> NSFont {
        MeadowFonts.register()
        guard let family = face.family else {
            let mono = NSFont.monospacedSystemFont(ofSize: size, weight: weight)
            return italic ? NSFontManager.shared.convert(mono, toHaveTrait: .italicFontMask) : mono
        }
        let nearest = MeadowFonts.nsWeight(css: face.weight(nearest: MeadowFonts.css(weight)))
        var traits: [NSFontDescriptor.TraitKey: Any] = [.weight: nearest]
        if italic { traits[.symbolic] = NSFontDescriptor.SymbolicTraits.italic.rawValue }
        let described = NSFontDescriptor(fontAttributes: [.family: family, .traits: traits])
        // Core Text answers a family it doesn't have with another; the system's face is the nearer stand-in.
        guard let font = NSFont(descriptor: described, size: size), font.familyName == family else {
            return .systemFont(ofSize: size, weight: weight)
        }
        return font
    }
}
#endif
