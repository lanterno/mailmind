#if os(macOS)
import SwiftUI

/// A card above the mail or on the brief: a rounded panel tinted with the accent colour, so what Claude wrote or what a
/// message proposes stands apart from the mail itself.
struct DeskCard<Content: View>: View {
    var tint: Color = .accentColor
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            content
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(tint.opacity(0.07), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).strokeBorder(tint.opacity(0.22)))
    }
}

/// A card's heading: its symbol and name, and whatever goes beside them.
struct DeskCardHeading<Trailing: View>: View {
    let title: LocalizedStringKey
    let symbol: String
    @ViewBuilder var trailing: Trailing

    var body: some View {
        HStack(spacing: 8) {
            Label(title, systemImage: symbol)
                .font(.callout.weight(.semibold))
                .foregroundStyle(.tint)
            trailing
            Spacer(minLength: 0)
        }
    }
}

extension DeskCardHeading where Trailing == EmptyView {
    init(title: LocalizedStringKey, symbol: String) {
        self.init(title: title, symbol: symbol) { EmptyView() }
    }
}

/// A card's foot: small, secondary words.
struct DeskCardFoot<Content: View>: View {
    @ViewBuilder let content: Content

    var body: some View {
        HStack(spacing: 6) {
            content
        }
        .font(.caption)
        .foregroundStyle(.secondary)
    }
}

/// Grey bars where words are coming, as the page's skeleton shows while Claude reads.
struct DeskWaitingBars: View {
    var lines = 3
    let saying: LocalizedStringKey

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(0..<lines, id: \.self) { line in
                RoundedRectangle(cornerRadius: 3)
                    .fill(.quaternary)
                    .frame(height: 9)
                    .frame(maxWidth: line == lines - 1 ? 180 : .infinity, alignment: .leading)
            }
            HStack(spacing: 6) {
                ProgressView().controlSize(.small)
                Text(saying)
            }
            .font(.caption)
            .foregroundStyle(.secondary)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Text(saying))
    }
}

/// A sender's initials in a circle tinted by their address, as the page's avatars are.
struct SenderInitials: View {
    let name: String?
    let address: String?
    var size: CGFloat = 28

    var body: some View {
        Text(Self.initials(name: name, address: address))
            .font(.system(size: size * 0.4, weight: .semibold))
            .foregroundStyle(.white)
            .frame(width: size, height: size)
            .background(Circle().fill(Self.hue(of: address ?? name ?? "")))
            .accessibilityHidden(true)
    }

    /// One or two letters: the first of the first and last words of a name, else of the address.
    static func initials(name: String?, address: String?) -> String {
        let words = (name ?? "").split(whereSeparator: { !$0.isLetter && !$0.isNumber }).filter { !$0.isEmpty }
        if let first = words.first {
            let last = words.count > 1 ? words.last?.first.map(String.init) ?? "" : ""
            return (String(first.prefix(1)) + last).uppercased()
        }
        return String((address ?? "?").prefix(1)).uppercased()
    }

    /// A steady hue for an address: the same sender is always the same colour.
    static func hue(of text: String) -> Color {
        var sum: UInt32 = 0
        for scalar in text.lowercased().unicodeScalars { sum = sum &* 31 &+ scalar.value }
        return Color(hue: Double(sum % 360) / 360, saturation: 0.45, brightness: 0.68)
    }
}
#endif
