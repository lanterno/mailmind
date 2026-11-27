import SwiftUI

// Meadow's segmented choice (#44), the mockup's `.seg` in its Reading popover: a few words side by side on a sunken
// strip, the one chosen raised on paper. Settings' light, rows and tones use it, the composer's draft tone, how the
// Document Hub shows its files, and Curate's parts and a block's reach.

/// One of a few choices (`.seg`, `.seg-b`): each a word, the chosen one on raised paper with a soft shadow. The
/// arrow keys go from one to the next.
struct MeadowSegmentedChoice<Value: Hashable>: View {
    let label: String
    @Binding var selection: Value
    let options: [(value: Value, title: String)]
    /// Each choice as wide as the widest, filling the strip; else each as wide as its words.
    var fills = true
    var symbol: (Value) -> String? = { _ in nil }
    @Environment(\.meadow) private var meadow

    var body: some View {
        HStack(spacing: 2) {
            ForEach(Array(options.enumerated()), id: \.offset) { index, option in
                MeadowSegment(title: option.title, symbol: symbol(option.value), chosen: option.value == selection, fills: fills) {
                    selection = option.value
                }
                .onKeyPress(.rightArrow) { step(from: index, by: 1) }
                .onKeyPress(.leftArrow) { step(from: index, by: -1) }
            }
        }
        .padding(3)
        .background(meadow.paper2, in: RoundedRectangle(cornerRadius: 10))
        .fixedSize(horizontal: !fills, vertical: true)
        .accessibilityElement(children: .contain)
        .accessibilityLabel(Text(label))
    }

    private func step(from index: Int, by: Int) -> KeyPress.Result {
        guard !options.isEmpty else { return .ignored }
        selection = options[(index + by + options.count) % options.count].value
        return .handled
    }
}

private struct MeadowSegment: View {
    let title: String
    let symbol: String?
    let chosen: Bool
    let fills: Bool
    let choose: () -> Void
    @Environment(\.meadow) private var meadow
    @FocusState private var focused: Bool
    @State private var hovering = false

    var body: some View {
        Button(action: choose) {
            HStack(spacing: 5) {
                if let symbol { Image(systemName: symbol).accessibilityHidden(true) }
                Text(title)
            }
                .font(.meadow(.ui, 13, .bold))
                .foregroundStyle(chosen || hovering ? meadow.ink : meadow.ink2)
                .lineLimit(1)
                .padding(.horizontal, 10)
                .frame(maxWidth: fills ? .infinity : nil, minHeight: 32)
                .background {
                    if chosen {
                        RoundedRectangle(cornerRadius: 8)
                            .fill(meadow.paperRaised)
                            .shadow(color: Color(meadow: 0x28283C, opacity: 0.18), radius: 1.5, y: 1)
                    }
                }
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .focused($focused)
        .focusEffectDisabled()
        .meadowFocusRing(focused, radius: 8)
        .onHover { hovering = $0 }
        .accessibilityAddTraits(chosen ? .isSelected : [])
    }
}
