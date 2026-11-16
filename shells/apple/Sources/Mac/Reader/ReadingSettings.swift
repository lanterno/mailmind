#if os(macOS)
import SwiftUI

/// Reading settings (Aa, in the pane's bar), as Meadow's reading menu lays them out (#44's `readingMenu`): the text's
/// size, its face, the line's length and the light, then mail's colours, each kept with the rest of the preferences
/// on the server. There is no setting for where filing moves on to: filing always opens the next message.
struct ReadingSettings: View {
    /// The keys while it is open in Meadow's popover.
    var keys = ReadingKeys()
    private var composing: Composing { .shared }
    private var status: AppStatus { .shared }
    @Environment(\.meadow) private var meadow

    private static let sizes: [API.ReadSize] = [.s, .m, .l, .xl]
    private static let faces: [API.ReadFont] = [.sans, .serif, .mono]
    private static let widths: [API.ReadWidth] = [.narrow, .medium, .wide]
    private static let themes = API.Theme.round
    private static let colours: [API.MailColors] = [.auto, .dark, .original]

    var body: some View {
        let prefs = ReadingPrefs(composing.preferences)
        VStack(alignment: .leading, spacing: 0) {
            Text("Reading".uppercased())
                .font(.meadow(.ui, 11, .heavy))
                .tracking(1.1)
                .foregroundStyle(meadow.ink3)
                .padding(.horizontal, 10)
                .padding(.top, 8)
                .padding(.bottom, 6)
                .accessibilityAddTraits(.isHeader)
            ReadingSet(label: String(localized: "Text size"), options: [
                .init(value: API.ReadSize.s, label: "A", look: .meadow(.ui, 12, .bold), name: "Small"),
                .init(value: .m, label: "A", look: .meadow(.ui, 14, .bold), name: "Medium"),
                .init(value: .l, label: "A", look: .meadow(.ui, 16, .bold), name: "Large"),
                .init(value: .xl, label: "A", look: .meadow(.ui, 18, .bold), name: "Extra large"),
            ], chosen: prefs.look.size, ringed: keys.ringed(0)) { change(API.ChangedPreferences(readSize: $0)) }
            ReadingSet(label: String(localized: "Typeface"), options: [
                .init(value: API.ReadFont.sans, label: "Sans", look: .meadow(.ui, 13, .bold)),
                .init(value: .serif, label: "Serif", look: .system(size: 13, weight: .bold, design: .serif)),
                .init(value: .mono, label: "Mono", look: .system(size: 12, weight: .bold, design: .monospaced)),
            ], chosen: prefs.look.font, ringed: keys.ringed(1)) { change(API.ChangedPreferences(readFont: $0)) }
            ReadingSet(label: String(localized: "Line length"), options: [
                .init(value: API.ReadWidth.narrow, label: "Narrow"),
                .init(value: .medium, label: "Medium"),
                .init(value: .wide, label: "Wide"),
            ], chosen: prefs.width, ringed: keys.ringed(2)) { change(API.ChangedPreferences(readWidth: $0)) }
            ReadingSet(label: String(localized: "Light"), options: Self.themes.map { theme in
                // Auto, as Mail colours says it, so four fit across the menu; VoiceOver says the whole name.
                ReadingSet<API.Theme>.Option(value: theme, label: theme == .auto ? String(localized: "Auto") : theme.name,
                                             name: theme.name)
            }, chosen: status.theme, ringed: keys.ringed(3)) { theme in
                status.setPreferences(API.ChangedPreferences(theme: theme))
            }
            .disabled(status.core == nil)
            ReadingSet(label: String(localized: "Mail colours"), options: [
                .init(value: API.MailColors.auto, label: "Auto"),
                .init(value: .dark, label: "Dark"),
                .init(value: .original, label: "Original"),
            ], chosen: prefs.look.colors, ringed: keys.ringed(4)) { change(API.ChangedPreferences(mailColors: $0)) }
            Text("""
                Size and face set plain-text mail; rich mail keeps the sender’s own type. \
                Auto shows rich mail in its own colours and plain text in the light’s.
                """)
                .font(.meadow(.ui, 12))
                .foregroundStyle(meadow.ink3)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 10)
                .padding(.bottom, 8)
        }
        .frame(width: 250)
        .meadowPop(width: 262)
        .onAppear {
            keys.at = nil
            keys.step = { row, by in step(row, by: by) }
        }
    }

    /// Choose the next or the one before in setting `row`, as ← and → do.
    private func step(_ row: Int, by: Int) {
        let prefs = ReadingPrefs(composing.preferences)
        switch row {
        case 0:
            if let size = Self.next(prefs.look.size, in: Self.sizes, by: by) { change(.init(readSize: size)) }
        case 1:
            if let face = Self.next(prefs.look.font, in: Self.faces, by: by) { change(.init(readFont: face)) }
        case 2:
            if let width = Self.next(prefs.width, in: Self.widths, by: by) { change(.init(readWidth: width)) }
        case 3:
            if status.core != nil, let theme = Self.next(status.theme, in: Self.themes, by: by) {
                status.setPreferences(.init(theme: theme))
            }
        default:
            if let colours = Self.next(prefs.look.colors, in: Self.colours, by: by) {
                change(.init(mailColors: colours))
            }
        }
    }

    /// The choice `by` along from `chosen`, if there is one that way.
    static func next<Value: Equatable>(_ chosen: Value, in values: [Value], by: Int) -> Value? {
        guard let at = values.firstIndex(of: chosen), values.indices.contains(at + by) else { return nil }
        return values[at + by]
    }

    private func change(_ changed: API.ChangedPreferences) {
        Task { await composing.change(changed) }
    }
}

/// The reading settings' keys while Meadow's popover holds them: its panel is never the key window, so they come
/// here through `meadowPopover`'s `keys`. ↓ and ↑ go from setting to setting, ringing the one they are on, and ← and
/// → choose in it.
@MainActor
@Observable
final class ReadingKeys {
    static let settings = 5
    /// The setting the keys are on: none until one is pressed, so a click alone rings nothing.
    var at: Int?
    @ObservationIgnored var step: ((Int, Int) -> Void)?

    func ringed(_ row: Int) -> Bool { at == row }

    /// Say whether the key was the settings'.
    func key(_ event: NSEvent) -> Bool {
        switch event.keyCode {
        case 125: at = at.map { ($0 + 1) % Self.settings } ?? 0
        case 126: at = at.map { ($0 - 1 + Self.settings) % Self.settings } ?? Self.settings - 1
        case 123, 124:
            let row = at ?? 0
            at = row
            step?(row, event.keyCode == 124 ? 1 : -1)
        default: return false
        }
        return true
    }
}

/// One setting (`.set`): its name over a row of choices (`.seg`), the one chosen raised.
private struct ReadingSet<Value: Hashable>: View {
    struct Option {
        let value: Value
        let label: String
        var look: Font = .meadow(.ui, 13, .bold)
        /// What VoiceOver says, where the label alone doesn't say it.
        var name: String?
    }

    let label: String
    let options: [Option]
    let chosen: Value
    /// Whether the keys are on it.
    var ringed = false
    let choose: (Value) -> Void
    @Environment(\.meadow) private var meadow

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label)
                .font(.meadow(.ui, 12.5, .bold))
                .foregroundStyle(meadow.ink2)
            HStack(spacing: 2) {
                ForEach(options, id: \.value) { option in
                    Button { if option.value != chosen { choose(option.value) } } label: {
                        Text(option.label).font(option.look)
                    }
                    .buttonStyle(SegmentStyle(on: option.value == chosen))
                    .focusEffectDisabled()
                    .accessibilityLabel(option.name ?? option.label)
                    .accessibilityAddTraits(option.value == chosen ? .isSelected : [])
                }
            }
            .padding(3)
            .background(meadow.paper2, in: RoundedRectangle(cornerRadius: 10))
            .meadowFocusRing(ringed, radius: 10)
            .accessibilityElement(children: .contain)
            .accessibilityLabel(label)
        }
        .padding(.horizontal, 10)
        .padding(.top, 6)
        .padding(.bottom, 10)
    }
}

/// A choice in a row (`.seg-b`): the chosen one on raised paper with a soft shadow, the rest flat.
private struct SegmentStyle: ButtonStyle {
    let on: Bool

    func makeBody(configuration: Configuration) -> some View {
        Segment(label: configuration.label, on: on)
    }
}

private struct Segment: View {
    let label: ButtonStyleConfiguration.Label
    let on: Bool
    @Environment(\.meadow) private var meadow
    @Environment(\.isFocused) private var focused
    @Environment(\.isEnabled) private var enabled
    @State private var hovering = false

    var body: some View {
        label
            .foregroundStyle(on || hovering ? meadow.ink : meadow.ink2)
            .lineLimit(1)
            .frame(maxWidth: .infinity, minHeight: 32)
            .background {
                if on {
                    RoundedRectangle(cornerRadius: 8)
                        .fill(meadow.paperRaised)
                        .shadow(color: Color(red: 40 / 255, green: 40 / 255, blue: 60 / 255, opacity: 0.18),
                                radius: 1.5, y: 1)
                }
            }
            .contentShape(RoundedRectangle(cornerRadius: 8))
            .opacity(enabled ? 1 : 0.45)
            .onHover { hovering = $0 && enabled }
            .meadowFocusRing(focused, radius: 8)
    }
}
#endif
