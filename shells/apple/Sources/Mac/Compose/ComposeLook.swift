#if os(macOS)
import AppKit
import SwiftUI

// The compose dialog's parts in Meadow's look (#44), as the mockup's `.compose` draws them: the dialog's paper, its
// hairlines, a field's row (`.fld`), the popovers its buttons open (`.pop`), a field to type in, and the text's look
// for the AppKit view that holds the message.

/// The dialog's paper (`.dialog`): raised paper with its grain, the whole window.
struct ComposePaper: View {
    @Environment(\.meadow) private var meadow

    var body: some View {
        ZStack {
            meadow.paperRaised
            MeadowGrain()
        }
    }
}

/// The hairline between the dialog's parts.
struct ComposeHairline: View {
    @Environment(\.meadow) private var meadow

    var body: some View {
        Rectangle().fill(meadow.line).frame(height: 1).accessibilityHidden(true)
    }
}

/// A field's row (`.fld`): its name in a column 72 wide, then what it holds, a hairline above.
struct ComposeFieldRow<Content: View>: View {
    let label: LocalizedStringKey
    @ViewBuilder let content: () -> Content
    @Environment(\.meadow) private var meadow

    init(_ label: LocalizedStringKey, @ViewBuilder content: @escaping () -> Content) {
        self.label = label
        self.content = content
    }

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Text(label)
                .font(.meadow(.ui, 13, .bold))
                .foregroundStyle(meadow.ink3)
                .frame(width: 72, height: 34, alignment: .leading)
                .accessibilityHidden(true)
            HStack(spacing: 0) { content() }
                .frame(maxWidth: .infinity, minHeight: 34, alignment: .leading)
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 6)
        .overlay(alignment: .top) { ComposeHairline() }
    }
}

/// A popover's content as Meadow's (`.pop`): its title in small capitals, then what it holds, on raised paper. The
/// composer's popovers that hold a field to type in stay the system's: Meadow's own (`meadowPopover`) hang in a
/// panel that never takes the keyboard.
struct ComposePop<Content: View>: View {
    let title: String
    @ViewBuilder let content: () -> Content
    @Environment(\.meadow) private var meadow

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            MeadowPaletteSection(title, pop: true)
            content()
        }
        .padding(6)
        .frame(minWidth: 250, alignment: .leading)
        .background(meadow.paperRaised)
        .presentationBackground(meadow.paperRaised)
    }
}

extension View {
    /// A field to type in, on the paper with a hairline round it, as Meadow's inputs sit.
    func meadowComposeField() -> some View {
        modifier(MeadowTypedField())
    }
}

private struct MeadowTypedField: ViewModifier {
    @Environment(\.meadow) private var meadow

    func body(content: Content) -> some View {
        content
            .textFieldStyle(.plain)
            .font(.meadow(.ui, 14))
            .foregroundStyle(meadow.ink)
            .padding(.horizontal, 10)
            .frame(minHeight: 32)
            .background(meadow.paper, in: RoundedRectangle(cornerRadius: 9))
            .overlay(RoundedRectangle(cornerRadius: 9).strokeBorder(meadow.line2))
    }
}

/// How the message's text looks in its AppKit view (`.cp-body`): the reading face at 17 points, 1.65 lines apart,
/// in the light's ink, with the sun's wash under a selection, 14 and 18 points in from the dialog's edges.
struct ComposeTextLook: Equatable {
    static let size: CGFloat = 17
    static let lineHeight: CGFloat = 1.65
    /// The text's distance from the view's edges: the mockup's padding, and half the line's leading above, as CSS
    /// sets the first line.
    static var inset: CGSize {
        CGSize(width: 18, height: 14 + (MeadowFace.read.lineSpacing(size: size, lineHeight: lineHeight) / 2).rounded())
    }

    let ink: NSColor
    let selection: NSColor
    /// Which light, so the look is worked out again only when it changes.
    let key: String

    init(_ palette: MeadowPalette, light: MeadowLight) {
        ink = NSColor(palette.ink)
        selection = NSColor(palette.sun).withAlphaComponent(0.35)
        key = "\(light.rawValue) \(palette.ink.description)"
    }

    var font: NSFont { .meadow(.read, Self.size) }

    var paragraph: NSParagraphStyle {
        let style = NSMutableParagraphStyle()
        style.lineSpacing = MeadowFace.read.lineSpacing(size: Self.size, lineHeight: Self.lineHeight)
        return style
    }

    var attributes: [NSAttributedString.Key: Any] {
        [.font: font, .foregroundColor: ink, .paragraphStyle: paragraph]
    }

    /// Dress the view in this look: what is typed next, and what it holds already.
    @MainActor
    func dress(_ view: NSTextView) {
        view.font = font
        view.textColor = ink
        view.insertionPointColor = ink
        view.defaultParagraphStyle = paragraph
        view.typingAttributes = attributes
        view.selectedTextAttributes = [.backgroundColor: selection]
        if let storage = view.textStorage, storage.length > 0 {
            storage.setAttributes(attributes, range: NSRange(location: 0, length: storage.length))
        }
    }

    static func == (a: ComposeTextLook, b: ComposeTextLook) -> Bool { a.key == b.key }
}
#endif
