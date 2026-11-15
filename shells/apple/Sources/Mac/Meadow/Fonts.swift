import SwiftUI
#if os(macOS)
import AppKit
#endif

// Meadow's typefaces (#44), the page's own (web/assets/fonts): Nunito Sans for the interface and the mail, Shippori
// Mincho for headings, Klee One for the brief's voice. Until the app bundles and registers them, each falls back to the
// system's face of the same weight.

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
}

extension Font {
    /// Text in one of Meadow's faces, at a size in points (the mockup's px) and a weight.
    static func meadow(_ face: MeadowFace, _ size: CGFloat, _ weight: Font.Weight = .regular) -> Font {
        guard let family = face.family else { return .system(size: size, weight: weight, design: .monospaced) }
        return .custom(family, size: size).weight(weight)
    }
}

#if os(macOS)
extension NSFont {
    /// The same for AppKit and Core Text (the list's rows draw their own text).
    static func meadow(_ face: MeadowFace, _ size: CGFloat, _ weight: NSFont.Weight = .regular) -> NSFont {
        guard let family = face.family else { return .monospacedSystemFont(ofSize: size, weight: weight) }
        let described = NSFontDescriptor(fontAttributes: [
            .family: family,
            .traits: [NSFontDescriptor.TraitKey.weight: weight],
        ])
        return NSFont(descriptor: described, size: size) ?? .systemFont(ofSize: size, weight: weight)
    }
}
#endif
