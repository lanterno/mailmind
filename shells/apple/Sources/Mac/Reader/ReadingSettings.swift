#if os(macOS)
import SwiftUI

/// Reading settings (Aa, in the pane's bar), as Meadow's reading menu lays them out (#44's `readingMenu`): the text's
/// size, its face, the line's length and the light, then mail's colours, each kept with the rest of the preferences
/// on the server. There is no setting for where filing moves on to: filing always opens the next message.
struct ReadingSettings: View {
    private var composing: Composing { .shared }
    private var status: AppStatus { .shared }
    @Environment(\.meadow) private var meadow

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
            ], chosen: prefs.look.size) { change(API.ChangedPreferences(readSize: $0)) }
            ReadingSet(label: String(localized: "Typeface"), options: [
                .init(value: API.ReadFont.sans, label: "Sans", look: .meadow(.ui, 13, .bold)),
                .init(value: .serif, label: "Serif", look: .system(size: 13, weight: .bold, design: .serif)),
                .init(value: .mono, label: "Mono", look: .system(size: 12, weight: .bold, design: .monospaced)),
            ], chosen: prefs.look.font) { change(API.ChangedPreferences(readFont: $0)) }
            ReadingSet(label: String(localized: "Line length"), options: [
                .init(value: API.ReadWidth.narrow, label: "Narrow"),
                .init(value: .medium, label: "Medium"),
                .init(value: .wide, label: "Wide"),
            ], chosen: prefs.width) { change(API.ChangedPreferences(readWidth: $0)) }
            ReadingSet(label: String(localized: "Light"), options: [MeadowLight.day, .dusk, .night].map { light in
                ReadingSet<MeadowLight>.Option(value: light, label: light.name)
            }, chosen: status.light) { light in
                status.setPreferences(API.ChangedPreferences(theme: light.theme))
            }
            .disabled(status.core == nil)
            ReadingSet(label: String(localized: "Mail colours"), options: [
                .init(value: API.MailColors.auto, label: "Auto"),
                .init(value: .dark, label: "Dark"),
                .init(value: .original, label: "Original"),
            ], chosen: prefs.look.colors) { change(API.ChangedPreferences(mailColors: $0)) }
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
        .padding(6)
        .frame(width: 262)
        .background(meadow.paperRaised)
    }

    private func change(_ changed: API.ChangedPreferences) {
        Task { await composing.change(changed) }
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
