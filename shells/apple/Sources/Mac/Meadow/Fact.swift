#if os(macOS)
import SwiftUI

/// A fact in a side card (`.fact`): its name, and its value at the line's end, a dashed hairline under it. Settings'
/// connections and a sender's card list theirs this way.
struct MeadowFact<Value: View>: View {
    let name: String
    var last = false
    @ViewBuilder let value: () -> Value
    @Environment(\.meadow) private var meadow

    init(_ name: String, last: Bool = false, @ViewBuilder value: @escaping () -> Value) {
        self.name = name
        self.last = last
        self.value = value
    }

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(name).foregroundStyle(meadow.ink3)
            Spacer(minLength: 8)
            value()
                .fontWeight(.bold)
                .foregroundStyle(meadow.ink)
                .monospacedDigit()
                .multilineTextAlignment(.trailing)
        }
        .font(.meadow(.ui, 13))
        .padding(.vertical, 6)
        .overlay(alignment: .bottom) {
            if !last {
                Line().stroke(meadow.line, style: StrokeStyle(lineWidth: 1, dash: [3, 3])).frame(height: 1)
            }
        }
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
#endif
