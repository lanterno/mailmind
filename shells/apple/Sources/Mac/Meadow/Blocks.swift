import SwiftUI

// Meadow's building blocks (#44), each as the mockup's CSS draws it: the paper panel and its shadows, the buttons, the
// keycap, the chips, the section label, the AI's mark, the tabs, the split button and the focus ring. Every screen
// builds on these, so a size or a colour is changed here once.

// MARK: Mixing colours

extension Color {
    /// `color-mix(in srgb, self p, other)`: the two mixed channel by channel as sRGB writes them, as the mockup mixes
    /// its tokens (the brand's wing, a tinted fill).
    func meadowMix(_ amount: Double, with other: Color,
                   in environment: EnvironmentValues = EnvironmentValues()) -> Color {
        let a = resolve(in: environment), b = other.resolve(in: environment)
        let p = Float(max(0, min(1, amount)))
        func mix(_ x: Float, _ y: Float) -> Double { Double(x * p + y * (1 - p)) }
        return Color(.sRGB, red: mix(a.red, b.red), green: mix(a.green, b.green), blue: mix(a.blue, b.blue),
                     opacity: mix(a.opacity, b.opacity))
    }
}

// MARK: Shadows

extension View {
    /// The CSS box-shadows of a rounded box: the drops and rings outside it, and the inset highlights inside it,
    /// beneath what the view draws, as a browser paints them.
    func meadowShadow(_ shadows: [MeadowShadow], radius: CGFloat) -> some View {
        background { MeadowShadowsOutside(shadows: shadows.filter { !$0.inset }, radius: radius) }
    }
}

/// The shadows outside a box: each one's shape is the box grown by its spread and moved by its offset, blurred by half
/// its blur (CSS's blur is twice the Gaussian's deviation), and shown only outside the box, where CSS paints it.
struct MeadowShadowsOutside: View {
    let shadows: [MeadowShadow]
    let radius: CGFloat

    var body: some View {
        GeometryReader { geometry in
            let size = geometry.size
            ZStack {
                ForEach(Array(shadows.enumerated()), id: \.offset) { _, shadow in
                    RoundedRectangle(cornerRadius: max(0, radius + shadow.spread), style: .circular)
                        .fill(shadow.color)
                        .frame(width: max(0, size.width + 2 * shadow.spread),
                               height: max(0, size.height + 2 * shadow.spread))
                        .offset(x: shadow.x, y: shadow.y)
                        .blur(radius: shadow.blur / 2)
                        .frame(width: size.width, height: size.height)
                }
            }
            .mask { MeadowOutside(radius: radius).fill(style: FillStyle(eoFill: true)) }
        }
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

/// Everything around a rounded box, far enough out for the widest shadow, and not the box itself.
struct MeadowOutside: Shape {
    let radius: CGFloat

    func path(in rect: CGRect) -> Path {
        var path = Path(rect.insetBy(dx: -160, dy: -160))
        path.addRoundedRect(in: rect, cornerSize: CGSize(width: radius, height: radius))
        return path
    }
}

/// The shadows inside a box (the panel's highlight along its top): the box less itself moved by the shadow's offset
/// and shrunk by its spread, blurred, and kept inside the box.
struct MeadowShadowsInside: View {
    let shadows: [MeadowShadow]
    let radius: CGFloat

    var body: some View {
        ZStack {
            ForEach(Array(shadows.enumerated()), id: \.offset) { _, shadow in
                MeadowInsetBand(radius: radius, shadow: shadow)
                    .fill(shadow.color, style: FillStyle(eoFill: true))
                    .blur(radius: shadow.blur / 2)
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: radius))
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

struct MeadowInsetBand: Shape {
    let radius: CGFloat
    let shadow: MeadowShadow

    func path(in rect: CGRect) -> Path {
        var path = Path(rect.insetBy(dx: -40, dy: -40))
        let hole = rect.insetBy(dx: shadow.spread, dy: shadow.spread).offsetBy(dx: shadow.x, dy: shadow.y)
        let corner = max(0, radius - shadow.spread)
        path.addRoundedRect(in: hole, cornerSize: CGSize(width: corner, height: corner))
        return path
    }
}

// MARK: The panel

/// A panel of paper on the sky (`.panel`): paper with its grain, radius 16, the palette's panel shadow, and what it
/// holds clipped to it. The rail's is 86% paper, the sky showing faintly through.
struct MeadowPanel: ViewModifier {
    var radius: CGFloat = 16
    var opacity: Double = 1
    /// A raised card's or a dialog's shadow (`--shadow-pop`) in place of a panel's.
    var raised = false
    @Environment(\.meadow) private var meadow

    func body(content: Content) -> some View {
        let shadows = raised ? meadow.shadowPop : meadow.shadow
        content
            .background {
                ZStack {
                    (raised ? meadow.paperRaised : meadow.paper).opacity(opacity)
                    MeadowGrain()
                    MeadowShadowsInside(shadows: shadows.filter(\.inset), radius: radius)
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: radius))
            .meadowShadow(shadows, radius: radius)
    }
}

extension View {
    /// Lay this on a panel of paper: a list's, the pane's, the rail's (at 86%), or a raised card's.
    func meadowPanel(radius: CGFloat = 16, opacity: Double = 1, raised: Bool = false) -> some View {
        modifier(MeadowPanel(radius: radius, opacity: opacity, raised: raised))
    }
}

// MARK: The focus ring

/// The focus ring (`:focus-visible`): 2 points of the palette's focus colour, 2 points out from the box, which the
/// mockup rounds to 6.
struct MeadowFocusRing: ViewModifier {
    var focused: Bool
    var radius: CGFloat = 6
    @Environment(\.meadow) private var meadow

    func body(content: Content) -> some View {
        content.overlay {
            if focused {
                RoundedRectangle(cornerRadius: radius + 3)
                    .stroke(meadow.focus, lineWidth: 2)
                    .padding(-3)
                    .allowsHitTesting(false)
            }
        }
    }
}

extension View {
    /// Draw Meadow's focus ring around this view while `focused`.
    func meadowFocusRing(_ focused: Bool, radius: CGFloat = 6) -> some View {
        modifier(MeadowFocusRing(focused: focused, radius: radius))
    }
}

// MARK: Buttons

/// Meadow's buttons (`.btn`), every kind the mockup has: the plain one on raised paper, primary (indigo), quiet (no
/// fill until hovered), ai (moss) and danger (rust on its wash); with `del` for Delete's rust words, `small` (`.sm`),
/// `icon` (square) and `pressed` (`aria-pressed`). The top bar's `.btn.glass` draws as the plain one: the mockup's
/// `.btn` comes after `.glass` in its sheet and takes back the fill and the border.
///
/// Every kind keeps the mockup's one-point border, clear where it shows none, so its words sit one point further in
/// than its padding says, as CSS's `border-box` has them.
struct MeadowButtonStyle: ButtonStyle {
    enum Kind: Sendable {
        case standard, primary, quiet, ai, danger
    }

    var kind: Kind = .standard
    var del = false
    var small = false
    var icon = false
    var pressed = false
    /// A square button's width, where it isn't its height (the split button's second part).
    var width: CGFloat?
    /// The side flat against a neighbour, in a split button.
    var joined: HorizontalEdge?

    func makeBody(configuration: Configuration) -> some View {
        MeadowButtonBody(style: self, label: configuration.label, isPressed: configuration.isPressed)
    }

    /// Its height (`min-height`), and a square button's width.
    var height: CGFloat { small ? 28 : 34 }
    var radius: CGFloat { small ? 8 : 10 }
    var font: Font { .meadow(.ui, small ? 12.5 : 13.5, .bold) }

    /// Its box: rounded, but square on the side it is joined on.
    var shape: UnevenRoundedRectangle {
        let leading = joined == .leading ? 0 : radius, trailing = joined == .trailing ? 0 : radius
        return UnevenRoundedRectangle(topLeadingRadius: leading, bottomLeadingRadius: leading,
                                      bottomTrailingRadius: trailing, topTrailingRadius: trailing)
    }
}

private struct MeadowButtonBody: View {
    let style: MeadowButtonStyle
    let label: ButtonStyleConfiguration.Label
    let isPressed: Bool
    @Environment(\.meadow) private var meadow
    @Environment(\.isEnabled) private var enabled
    @Environment(\.isFocused) private var focused
    @Environment(\.accessibilityReduceMotion) private var still
    @State private var hovering = false

    var body: some View {
        let look = look
        HStack(spacing: 7) { label }
            .font(style.font)
            .lineLimit(1)
            .foregroundStyle(look.ink)
            .environment(\.meadowOnPrimary, style.kind == .primary && !style.pressed)
            .padding(.horizontal, style.icon ? 0 : (style.small ? 9 : 12) + 1)
            .frame(minWidth: style.icon ? (style.width ?? style.height) : nil,
                   maxWidth: style.icon ? (style.width ?? style.height) : nil, minHeight: style.height)
            .background {
                style.shape
                    .fill(look.fill)
                    .overlay { style.shape.strokeBorder(look.border, lineWidth: 1) }
                    .overlay {
                        if style.kind == .primary {
                            // The primary's highlight along its top edge.
                            MeadowShadowsInside(shadows: [MeadowButtonBody.highlight], radius: style.radius)
                        }
                    }
                    .background {
                        if style.kind == .primary {
                            MeadowShadowsOutside(shadows: [.init(x: 0, y: 8, blur: 18, spread: -10,
                                                                 color: meadow.primary, inset: false)],
                                                 radius: style.radius)
                        }
                    }
            }
            .contentShape(style.shape)
            .offset(y: isPressed ? 1 : 0)
            .opacity(enabled ? 1 : 0.45)
            .meadowFocusRing(focused)
            .onHover { hovering = $0 && enabled }
            .animation(still ? nil : .easeOut(duration: 0.12), value: hovering)
    }

    static let highlight = MeadowShadow(x: 0, y: 1, blur: 0, spread: 0, color: Color(meadow: 0xFFFFFF, opacity: 0.16),
                                        inset: true)

    private var look: (fill: Color, border: Color, ink: Color) {
        let hover = hovering
        var fill: Color, border: Color, ink: Color
        switch style.kind {
        case .standard:
            (fill, border, ink) = (hover ? meadow.paper3 : meadow.paperRaised, meadow.line2, meadow.ink)
        case .primary:
            (fill, border, ink) = (hover ? meadow.primaryHi : meadow.primary, .clear, meadow.onPrimary)
        case .quiet:
            (fill, border, ink) = (hover ? meadow.paper3 : .clear, .clear, hover ? meadow.ink : meadow.ink2)
        case .ai:
            (fill, border, ink) = (meadow.mossWash, hover ? meadow.moss : meadow.moss.opacity(0.3), meadow.moss)
        case .danger:
            (fill, border, ink) = (meadow.rustWash, meadow.rust.opacity(0.4), meadow.rust)
        }
        if style.pressed, !(hover && style.kind == .quiet) {
            (fill, ink) = (meadow.primaryWash, meadow.primaryInk)
        }
        if style.del {
            ink = meadow.rust
            if hover { fill = meadow.rustWash }
        }
        return (fill, border, ink)
    }
}

extension View {
    /// Style a button as one of Meadow's, with its own focus ring in place of the system's.
    func meadowButton(_ kind: MeadowButtonStyle.Kind = .standard, del: Bool = false, small: Bool = false,
                      icon: Bool = false, pressed: Bool = false) -> some View {
        buttonStyle(MeadowButtonStyle(kind: kind, del: del, small: small, icon: icon, pressed: pressed))
            .focusEffectDisabled()
    }
}

// MARK: The keycap

private struct MeadowOnPrimaryKey: EnvironmentKey {
    static let defaultValue = false
}

extension EnvironmentValues {
    /// Inside a primary button, where a keycap is only its outline in the button's words' colour.
    var meadowOnPrimary: Bool {
        get { self[MeadowOnPrimaryKey.self] }
        set { self[MeadowOnPrimaryKey.self] = newValue }
    }
}

/// A key, as the mockup's `kbd` draws one: at least 19 square, a hairline with a heavier bottom edge, on paper.
struct MeadowKey: View {
    let key: String
    @Environment(\.meadow) private var meadow
    @Environment(\.meadowOnPrimary) private var onPrimary

    init(_ key: String) {
        self.key = key
    }

    var body: some View {
        Text(verbatim: key)
            .font(.meadow(.mono, 10.5, .bold))
            .foregroundStyle(onPrimary ? meadow.onPrimary : meadow.ink3)
            .lineLimit(1)
            .fixedSize()
            .padding(.horizontal, 4)
            .frame(minWidth: 17, minHeight: 16)
            .padding(.horizontal, 1)
            .padding(.top, 1)
            .padding(.bottom, 2)
            .background {
                ZStack {
                    if !onPrimary {
                        MeadowKeyFace(inner: true).fill(meadow.paper)
                    }
                    MeadowKeyFace(inner: false).fill(onPrimary ? meadow.onPrimary.opacity(0.35) : meadow.line2,
                                                     style: FillStyle(eoFill: true))
                }
            }
            .accessibilityLabel(Text(verbatim: key))
    }
}

/// A keycap's border (1 point, 2 at the bottom, radius 5) as a ring, or the face inside it.
private struct MeadowKeyFace: Shape {
    let inner: Bool

    func path(in rect: CGRect) -> Path {
        let face = CGRect(x: rect.minX + 1, y: rect.minY + 1, width: rect.width - 2, height: rect.height - 3)
        var path = Path()
        if !inner { path.addRoundedRect(in: rect, cornerSize: CGSize(width: 5, height: 5)) }
        path.addRoundedRect(in: face, cornerSize: CGSize(width: 4, height: 4))
        return path
    }
}

// MARK: Chips

/// A chip (`.chip`): a pill of words, plain, in sunlight for what needs you, in moss for the AI's, with an account's
/// dot, or only its outline.
struct MeadowChip: View {
    enum Tone {
        case plain
        case sun
        case moss
        case account(Color)
        case line
    }

    let text: String
    var tone: Tone = .plain
    var icon: MeadowIcon?
    @Environment(\.meadow) private var meadow

    init(_ text: String, tone: Tone = .plain, icon: MeadowIcon? = nil) {
        self.text = text
        self.tone = tone
        self.icon = icon
    }

    var body: some View {
        HStack(spacing: 5) {
            if let dot { Circle().fill(dot).frame(width: 7, height: 7) }
            if let icon { MeadowIconView(icon, size: 13) }
            Text(text)
        }
        .font(.meadow(.ui, 11.5, .bold))
        .lineLimit(1)
        .foregroundStyle(ink)
        .padding(.horizontal, 8)
        .frame(height: 22)
        .background(Capsule().fill(fill))
        .overlay {
            if case .line = tone { Capsule().strokeBorder(meadow.line2, lineWidth: 1) }
        }
        .fixedSize()
    }

    private var dot: Color? {
        switch tone {
        case .sun: meadow.sun
        case .account(let color): color
        default: nil
        }
    }

    private var fill: Color {
        switch tone {
        case .sun: meadow.sunWash
        case .moss: meadow.mossWash
        case .line: .clear
        default: meadow.paper3
        }
    }

    private var ink: Color {
        switch tone {
        case .sun: meadow.sunInk
        case .moss: meadow.moss
        default: meadow.ink2
        }
    }
}

// MARK: Labels

/// A section's label (`.label`): small capitals, spaced a tenth apart, in the quietest ink.
struct MeadowSectionLabel: View {
    let text: String
    @Environment(\.meadow) private var meadow

    init(_ text: String) {
        self.text = text
    }

    var body: some View {
        Text(text.uppercased())
            .font(.meadow(.ui, 11, .heavy))
            .tracking(1.1)
            .foregroundStyle(meadow.ink3)
            .accessibilityLabel(Text(text))
            .accessibilityAddTraits(.isHeader)
    }
}

/// The AI's mark (`.ai-mark`): the wind and its label, in moss, which is only ever the AI's voice.
struct MeadowAIMark: View {
    let text: String
    @Environment(\.meadow) private var meadow

    init(_ text: String) {
        self.text = text
    }

    var body: some View {
        HStack(spacing: 6) {
            MeadowIconView(.wind, size: 16)
            Text(text.uppercased()).tracking(1.1)
        }
        .font(.meadow(.ui, 11, .heavy))
        .foregroundStyle(meadow.moss)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Text(text))
    }
}

// MARK: Tabs

/// Tabs over a hairline (`.tabs`, `.tab`): the one chosen in ink with an indigo bar under it, each with its count,
/// which is in sunlight where it counts what needs you. The arrow keys go from one to the next.
struct MeadowTabs<Value: Hashable>: View {
    struct Tab {
        let value: Value
        let title: String
        var count: String?
        var sun = false
    }

    let tabs: [Tab]
    @Binding var selection: Value
    var label: String = ""
    @Environment(\.meadow) private var meadow

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 2) {
                ForEach(Array(tabs.enumerated()), id: \.offset) { index, tab in
                    MeadowTabButton(tab: tab, chosen: tab.value == selection) { selection = tab.value }
                        .onKeyPress(.rightArrow) { step(from: index, by: 1) }
                        .onKeyPress(.leftArrow) { step(from: index, by: -1) }
                }
                Spacer(minLength: 0)
            }
            .frame(height: 36)
            Rectangle().fill(meadow.line).frame(height: 1)
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel(Text(label))
    }

    private func step(from index: Int, by: Int) -> KeyPress.Result {
        guard !tabs.isEmpty else { return .ignored }
        selection = tabs[(index + by + tabs.count) % tabs.count].value
        return .handled
    }
}

private struct MeadowTabButton<Value: Hashable>: View {
    let tab: MeadowTabs<Value>.Tab
    let chosen: Bool
    let choose: () -> Void
    @Environment(\.meadow) private var meadow
    @FocusState private var focused: Bool
    @State private var hovering = false

    var body: some View {
        Button(action: choose) {
            HStack(spacing: 6) {
                Text(tab.title)
                if let count = tab.count {
                    Text(count).monospacedDigit().foregroundStyle(tab.sun ? meadow.sunInk : meadow.ink3)
                }
            }
            .font(.meadow(.ui, 13.5, .bold))
            .foregroundStyle(chosen || hovering ? meadow.ink : meadow.ink3)
            .padding(.horizontal, 9)
            .frame(height: 36)
            .contentShape(Rectangle())
            .overlay(alignment: .bottom) {
                if chosen {
                    RoundedRectangle(cornerRadius: 2)
                        .fill(meadow.primary)
                        .frame(height: 2.5)
                        .padding(.horizontal, 7)
                        .offset(y: 1)
                }
            }
        }
        .buttonStyle(.plain)
        .focused($focused)
        .focusEffectDisabled()
        .meadowFocusRing(focused)
        .onHover { hovering = $0 }
        .accessibilityAddTraits(chosen ? [.isSelected] : [])
    }
}

// MARK: The split button

/// A button with a narrow second part for its other choices (`.split`), as Send has Send later: the two share one
/// rounded box, a hairline between them.
struct MeadowSplitButton<Label: View>: View {
    var kind: MeadowButtonStyle.Kind = .primary
    let action: () -> Void
    @ViewBuilder let label: () -> Label
    /// What the second part says to VoiceOver, and what it does.
    let moreLabel: String
    let more: () -> Void
    @Environment(\.meadow) private var meadow

    var body: some View {
        HStack(spacing: 0) {
            Button(action: action, label: label)
                .buttonStyle(MeadowButtonStyle(kind: kind, joined: .trailing))
                .focusEffectDisabled()
            Button(action: more) { MeadowIconView(.down) }
                .buttonStyle(MeadowButtonStyle(kind: kind, icon: true, width: 32, joined: .leading))
                .focusEffectDisabled()
                .overlay(alignment: .leading) {
                    Rectangle().fill(meadow.onPrimary.opacity(0.25)).frame(width: 1)
                }
                .accessibilityLabel(Text(moreLabel))
        }
        .fixedSize()
    }
}
