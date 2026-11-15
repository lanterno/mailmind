#if os(macOS)
import SwiftUI

/// A fact in a side card (`.fact`): its name in the card's ink, and its value at the line's end in bold, a dashed
/// hairline under all but the last. The reader's cards, the paper trail's, Settings' connections and a sender's card
/// list theirs this way. A name is words, or a view of its own such as an account's chip.
struct MeadowFact<Name: View, Value: View>: View {
    var last = false
    @ViewBuilder let name: () -> Name
    @ViewBuilder let value: () -> Value
    @Environment(\.meadow) private var meadow

    init(last: Bool = false, @ViewBuilder name: @escaping () -> Name, @ViewBuilder value: @escaping () -> Value) {
        self.last = last
        self.name = name
        self.value = value
    }

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            name()
            Spacer(minLength: 8)
            value()
                .fontWeight(.bold)
                .monospacedDigit()
                .multilineTextAlignment(.trailing)
        }
        .font(.meadow(.ui, 13))
        .foregroundStyle(meadow.ink)
        .padding(.vertical, 5)
        .overlay(alignment: .bottom) {
            if !last {
                Line().stroke(meadow.line, style: StrokeStyle(lineWidth: 1, dash: [3, 3])).frame(height: 1)
            }
        }
        .accessibilityElement(children: .combine)
    }

    private struct Line: Shape {
        func path(in rect: CGRect) -> Path {
            Path {
                $0.move(to: CGPoint(x: rect.minX, y: rect.midY))
                $0.addLine(to: CGPoint(x: rect.maxX, y: rect.midY))
            }
        }
    }
}

extension MeadowFact where Name == Text {
    /// A fact named in words.
    init(_ name: String, last: Bool = false, @ViewBuilder value: @escaping () -> Value) {
        self.init(last: last, name: { Text(verbatim: name) }, value: value)
    }
}

extension MeadowFact where Value == Text {
    /// A fact whose value is words, as the reader's and the paper trail's are: an amount, a size, a count.
    init(value: String, last: Bool = false, @ViewBuilder name: @escaping () -> Name) {
        self.init(last: last, name: name, value: { Text(verbatim: value) })
    }
}

extension MeadowFact where Name == Text, Value == Text {
    init(_ name: String, value: String, last: Bool = false) {
        self.init(last: last, name: { Text(verbatim: name) }, value: { Text(verbatim: value) })
    }
}
#endif
