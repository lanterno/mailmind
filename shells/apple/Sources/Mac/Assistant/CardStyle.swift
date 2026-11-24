#if os(macOS)
import SwiftUI

/// A card on the desk, as Meadow draws its cards: a side card of raised paper with a hairline (`.side-card`), the
/// larger card a message that needs you sits on (`.need`), or the moss wash of a note in the AI's voice (`.note`).
struct DeskCard<Content: View>: View {
    enum Look {
        case side, need, note
    }

    var look: Look = .side
    /// Kept for the callers that tinted the card before Meadow: its cards are paper, whatever the tint.
    var tint: Color?
    @ViewBuilder let content: Content
    @Environment(\.meadow) private var meadow

    var body: some View {
        let radius: CGFloat = look == .need ? 16 : 14
        VStack(alignment: .leading, spacing: 8) {
            content
        }
        .font(.meadow(.ui, 13))
        .foregroundStyle(meadow.ink2)
        .padding(.horizontal, 16)
        .padding(.top, 14)
        .padding(.bottom, look == .note ? 12 : 14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background {
            if look == .note {
                ZStack {
                    meadow.mossWash
                    MeadowGrain()
                }
                .clipShape(RoundedRectangle(cornerRadius: radius))
            } else {
                RoundedRectangle(cornerRadius: radius).fill(meadow.paperRaised)
            }
        }
        .overlay {
            if look != .note { RoundedRectangle(cornerRadius: radius).strokeBorder(meadow.line, lineWidth: 1) }
        }
    }
}

/// A side card with its heading (`.side-card h3`): a small icon and a label in small capitals, then what it holds.
struct DeskSideCard<Content: View, Trailing: View>: View {
    let title: String
    let icon: MeadowIcon
    @ViewBuilder var trailing: Trailing
    @ViewBuilder let content: Content
    @Environment(\.meadow) private var meadow

    var body: some View {
        DeskCard(look: .side) {
            HStack(spacing: 8) {
                MeadowIconView(icon, size: 15)
                    .foregroundStyle(meadow.ink3)
                MeadowSectionLabel(title)
                Spacer(minLength: 0)
                trailing
            }
            .padding(.bottom, 2)
            content
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel(Text(title))
    }
}

extension DeskSideCard where Trailing == EmptyView {
    init(title: String, icon: MeadowIcon, @ViewBuilder content: () -> Content) {
        self.init(title: title, icon: icon, trailing: { EmptyView() }, content: content)
    }
}

/// A card's heading: the AI's mark for what the AI wrote, else the section's icon and label, and what goes beside.
struct DeskCardHeading<Trailing: View>: View {
    let title: String
    let icon: MeadowIcon
    /// In the AI's voice: the wind and moss.
    var ai = false
    @ViewBuilder var trailing: Trailing
    @Environment(\.meadow) private var meadow

    init(title: String, icon: MeadowIcon, ai: Bool = false, @ViewBuilder trailing: () -> Trailing) {
        self.title = title
        self.icon = icon
        self.ai = ai
        self.trailing = trailing()
    }

    /// The heading as the cards named it before Meadow, by an SF Symbol's name: "sparkles" was the AI's.
    init(title: String, symbol: String, @ViewBuilder trailing: () -> Trailing) {
        self.init(title: title, icon: Self.icon(symbol), ai: symbol == "sparkles", trailing: trailing)
    }

    var body: some View {
        HStack(spacing: 8) {
            if ai {
                MeadowAIMark(title)
            } else {
                MeadowIconView(icon, size: 15).foregroundStyle(meadow.ink3)
                MeadowSectionLabel(title)
            }
            trailing
            Spacer(minLength: 0)
        }
    }

    /// Meadow's icon for a symbol the cards named.
    static func icon(_ symbol: String) -> MeadowIcon {
        switch symbol {
        case "sparkles": .wind
        case "checklist": .todo
        case "calendar": .cal
        case "hourglass": .hourglass
        case "checkmark.circle": .checkCircle
        default: .info
        }
    }
}

extension DeskCardHeading where Trailing == EmptyView {
    init(title: String, icon: MeadowIcon, ai: Bool = false) {
        self.init(title: title, icon: icon, ai: ai) { EmptyView() }
    }

    init(title: String, symbol: String) {
        self.init(title: title, symbol: symbol) { EmptyView() }
    }
}

/// A card's foot (`.note .ft`): small words in the quietest ink.
struct DeskCardFoot<Content: View>: View {
    @ViewBuilder let content: Content
    @Environment(\.meadow) private var meadow

    var body: some View {
        HStack(spacing: 6) {
            content
        }
        .font(.meadow(.ui, 12))
        .foregroundStyle(meadow.ink3)
    }
}

/// Bars of paper where words are coming, and the AI's "thinking" in moss (`.thinking`), while the AI reads.
struct DeskWaitingBars: View {
    var lines = 3
    let saying: LocalizedStringKey
    @Environment(\.meadow) private var meadow

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            ForEach(0..<lines, id: \.self) { line in
                Capsule()
                    .fill(meadow.moss.opacity(0.14))
                    .frame(height: 9)
                    .frame(maxWidth: line == lines - 1 ? 180 : .infinity, alignment: .leading)
            }
            DeskThinking(saying: saying)
                .padding(.top, 2)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Text(saying))
    }
}

/// The AI at work (`.thinking`): a ring with a gap, and what it is doing, in moss. The ring stands still: nothing
/// turns on the main thread while a person waits.
struct DeskThinking: View {
    let saying: LocalizedStringKey
    @Environment(\.meadow) private var meadow

    var body: some View {
        HStack(spacing: 10) {
            Circle()
                .trim(from: 0, to: 0.75)
                .stroke(meadow.moss, style: StrokeStyle(lineWidth: 2, lineCap: .round))
                .frame(width: 12, height: 12)
                .rotationEffect(.degrees(-90))
            Text(saying)
        }
        .font(.meadow(.ui, 13.5, .semibold))
        .foregroundStyle(meadow.moss)
    }
}

/// A run of quiet words with a link-like button in it (`.linkbtn`): the primary's ink, underlined.
struct DeskLinkButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        DeskLinkBody(label: configuration.label)
    }
}

private struct DeskLinkBody: View {
    let label: ButtonStyleConfiguration.Label
    @Environment(\.meadow) private var meadow
    @Environment(\.isFocused) private var focused
    @State private var hovering = false

    var body: some View {
        label
            .font(.meadow(.ui, 12.5, .bold))
            .foregroundStyle(meadow.primaryInk)
            .underline(true, color: meadow.primaryInk)
            .padding(.horizontal, 4)
            .padding(.vertical, 2)
            .background(RoundedRectangle(cornerRadius: 6).fill(hovering ? meadow.paper3 : .clear))
            .contentShape(Rectangle())
            .meadowFocusRing(focused)
            .onHover { hovering = $0 }
    }
}

extension View {
    /// A button that reads as a link in a line of words (`.linkbtn`).
    func deskLink() -> some View {
        buttonStyle(DeskLinkButtonStyle()).focusEffectDisabled()
    }
}

/// A title that is a button (`.need h3 button`): plain words in ink that underline under the pointer, with the focus
/// ring when the keyboard is on it.
struct DeskTitleButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        DeskTitleBody(label: configuration.label)
    }
}

private struct DeskTitleBody: View {
    let label: ButtonStyleConfiguration.Label
    @Environment(\.isFocused) private var focused
    @State private var hovering = false

    var body: some View {
        label
            .underline(hovering)
            .contentShape(Rectangle())
            .meadowFocusRing(focused, radius: 4)
            .onHover { hovering = $0 }
    }
}

extension View {
    /// A title that opens what it names.
    func deskTitle() -> some View {
        buttonStyle(DeskTitleButtonStyle()).focusEffectDisabled()
    }
}

extension MeadowPalette {
    /// An account's colour by the place kept for it (`AccountPlaces`), as the rail and the rows colour it.
    func account(of account: String, among accounts: [String]) -> Color {
        self.account(AccountPlaces.place(of: account, among: accounts))
    }
}

/// An empty or failed place (`.lempty`): a quiet icon, a line in the display face, what happens next in a sentence,
/// and what can be done about it.
struct DeskEmpty<Actions: View>: View {
    var icon: MeadowIcon?
    let title: String
    var words: String?
    @ViewBuilder var actions: Actions
    @Environment(\.meadow) private var meadow

    var body: some View {
        VStack(spacing: 0) {
            if let icon {
                MeadowIconView(icon, size: 22)
                    .foregroundStyle(meadow.ink3)
                    .frame(width: 44, height: 44)
                    .background(Circle().fill(meadow.paper2))
                    .padding(.bottom, 12)
            }
            Text(title)
                .font(.meadow(.display, 18, .bold))
                .foregroundStyle(meadow.ink)
                .padding(.bottom, 6)
                .accessibilityAddTraits(.isHeader)
            if let words {
                Text(words)
                    .font(.meadow(.ui, 13.5))
                    .foregroundStyle(meadow.ink2)
                    .fixedSize(horizontal: false, vertical: true)
            }
            actions
                .padding(.top, 12)
        }
        .multilineTextAlignment(.center)
        .padding(.horizontal, 18)
        .padding(.vertical, 28)
        .frame(maxWidth: .infinity)
    }
}

extension DeskEmpty where Actions == EmptyView {
    init(icon: MeadowIcon? = nil, title: String, words: String? = nil) {
        self.init(icon: icon, title: title, words: words) { EmptyView() }
    }
}

/// A field on raised paper (the mockup's `.search`): a hairline, rounded, the focus ring while typing in it. The
/// caller holds the field's focus, which it often needs for itself.
private struct DeskField: ViewModifier {
    let focused: Bool
    @Environment(\.meadow) private var meadow

    func body(content: Content) -> some View {
        content
            .textFieldStyle(.plain)
            .font(.meadow(.ui, 14.5))
            .foregroundStyle(meadow.ink)
            .padding(.horizontal, 12)
            .padding(.vertical, 9)
            .frame(minHeight: 38)
            .background(RoundedRectangle(cornerRadius: 10).fill(meadow.paperRaised))
            .overlay { RoundedRectangle(cornerRadius: 10).strokeBorder(meadow.line2) }
            .meadowFocusRing(focused, radius: 10)
    }
}

extension View {
    /// A text field as Meadow draws one, ringed while `focused`.
    func deskField(focused: Bool) -> some View {
        modifier(DeskField(focused: focused))
    }
}
#endif
