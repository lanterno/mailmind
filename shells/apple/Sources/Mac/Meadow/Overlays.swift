#if os(macOS)
import AppKit
import Observation
import SwiftUI

// What floats over Meadow's window (#44), as the mockup's overlay CSS draws it: the scrim and the dialog on it
// (`.scrim`, `.dialog`), the palette's parts (`.pal-in`, `.pal-sec`, `.pal-i`, `.pal-ft`), the popover (`.pop`, `.ttl`,
// `hr`, `.health`) and the panel it hangs in, over every panel of the window at the place its button is, unclipped by
// the panel it was opened from (`MeadowPopoverAnchor`), and the toast (`.toast`).

// MARK: The dialog

/// Where a dialog stands: its width at most, and how far down the window its top is (`top: 11vh` for the palette).
struct MeadowDialogPlace: Equatable, Sendable {
    var width: CGFloat
    var top: Double

    static let palette = MeadowDialogPlace(width: 700, top: 0.11)
    static let keys = MeadowDialogPlace(width: 800, top: 0.09)
    static let form = MeadowDialogPlace(width: 520, top: 0.11)
    /// A card about someone: as wide as the reader's side card was as a sheet.
    static let card = MeadowDialogPlace(width: 420, top: 0.11)

    /// Its frame in a window of `size`: centred, 16 in from each side at least, `top` of the height down.
    func frame(in size: CGSize) -> (x: CGFloat, y: CGFloat, width: CGFloat) {
        let width = max(0, min(self.width, size.width - 32))
        return ((size.width - width) / 2, (size.height * top).rounded(), width)
    }
}

/// A dialog on the window's scrim (`.scrim`, `.dialog`): the window dimmed behind it, and the dialog of raised paper
/// with its grain, radius 18 and the popover's shadow, at its place. A click on the scrim closes it.
struct MeadowDialog<Content: View>: View {
    var place: MeadowDialogPlace = .palette
    let close: () -> Void
    @ViewBuilder let content: () -> Content
    @Environment(\.meadow) private var meadow

    var body: some View {
        GeometryReader { window in
            let frame = place.frame(in: window.size)
            ZStack(alignment: .topLeading) {
                meadow.scrim
                    .contentShape(Rectangle())
                    .onTapGesture(perform: close)
                    .accessibilityLabel("Close")
                    .accessibilityAddTraits(.isButton)
                content()
                    .environment(\.meadowDialogRoom, window.size.height)
                    .frame(width: frame.width, alignment: .topLeading)
                    .fixedSize(horizontal: false, vertical: true)
                    .meadowPanel(radius: 18, raised: true)
                    .offset(x: frame.x, y: frame.y)
                    .accessibilityElement(children: .contain)
                    .accessibilityAddTraits(.isModal)
            }
        }
    }
}

extension EnvironmentValues {
    /// The height of the window a dialog stands in, which a dialog's scrolling part keeps within (`60vh`).
    @Entry var meadowDialogRoom: CGFloat = 900
}

// MARK: The palette's parts

/// The palette's input (`.pal-in`): the search icon, a field in 17 point, and Esc's key, over a hairline.
struct MeadowPaletteField: View {
    let prompt: String
    @Binding var text: String
    /// Where the cursor is: after what the field opened on, not all of it chosen, as a field's focus would have it.
    @Binding var selection: TextSelection?
    var focus: FocusState<Bool>.Binding
    @Environment(\.meadow) private var meadow

    var body: some View {
        HStack(spacing: 12) {
            MeadowIconView(.search, size: 22)
                .foregroundStyle(meadow.ink)
            ZStack(alignment: .leading) {
                // The prompt in the light's ink: a field's own placeholder takes AppKit's grey.
                if text.isEmpty {
                    Text(prompt)
                        .foregroundStyle(meadow.ink3)
                        .lineLimit(1)
                        .allowsHitTesting(false)
                        .accessibilityHidden(true)
                }
                TextField("", text: $text, selection: $selection)
                    .textFieldStyle(.plain)
                    .foregroundStyle(meadow.ink)
                    .focused(focus)
                    .accessibilityLabel(prompt)
            }
            .font(.meadow(.ui, 17))
            .frame(maxWidth: .infinity)
            MeadowKey("esc")
        }
        .padding(.vertical, 14)
        .padding(.horizontal, 16)
        .frame(minHeight: 54)
        .overlay(alignment: .bottom) { Rectangle().fill(meadow.line).frame(height: 1) }
    }
}

/// A section's head in the palette or a popover (`.pal-sec`, `.ttl`).
struct MeadowPaletteSection: View {
    let title: String
    /// A popover's title sits a little closer to its first item.
    var pop = false

    init(_ title: String, pop: Bool = false) {
        self.title = title
        self.pop = pop
    }

    var body: some View {
        MeadowSectionLabel(title)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.top, pop ? 8 : 10)
            .padding(.horizontal, 10)
            .padding(.bottom, pop ? 6 : 4)
    }
}

/// One thing to pick (`.pal-i`): its icon, its words and a quieter line under them, and its keys on the right. The
/// one picked is in indigo's wash; in a popover, the one the pointer is over is a step darker and the one the keys
/// are on has the focus ring.
struct MeadowPaletteRow<Leading: View>: View {
    let title: String
    var detail: String?
    var keys: [String] = []
    var picked = false
    /// In a popover (`.pop .pal-i`): 38 high, not 40, and the pointer's paper.
    var pop = false
    var danger = false
    /// A tick on the right, for the one chosen.
    var check = false
    @ViewBuilder let leading: () -> Leading
    @Environment(\.meadow) private var meadow
    @Environment(\.isEnabled) private var enabled
    @State private var hovering = false

    var body: some View {
        HStack(spacing: 12) {
            leading()
                .foregroundStyle(danger ? meadow.rust : meadow.ink3)
            VStack(alignment: .leading, spacing: 0) {
                Text(title)
                    .foregroundStyle(danger ? meadow.rust : meadow.ink)
                    .frame(minHeight: 20, alignment: .leading)
                if let detail, !detail.isEmpty {
                    Text(detail)
                        .font(.meadow(.ui, 12.5))
                        .foregroundStyle(meadow.ink3)
                        .frame(minHeight: 20, alignment: .leading)
                }
            }
            .lineLimit(1)
            Spacer(minLength: 0)
            if !keys.isEmpty {
                HStack(spacing: 4) {
                    ForEach(Array(keys.enumerated()), id: \.offset) { _, key in MeadowKey(key) }
                }
            }
            if check {
                MeadowIconView(.check).foregroundStyle(meadow.ink3)
            }
        }
        .font(.meadow(.ui, 14))
        .padding(.vertical, 4)
        .padding(.horizontal, 10)
        .frame(minHeight: pop ? 38 : 40)
        .background {
            RoundedRectangle(cornerRadius: 10)
                .fill(!pop && picked ? meadow.primaryWash : pop && hovering && enabled ? meadow.paper3 : .clear)
        }
        .meadowFocusRing(pop && picked, radius: 10)
        .contentShape(Rectangle())
        .opacity(enabled ? 1 : 0.45)
        .onHover { hovering = $0 }
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(picked ? [.isSelected, .isButton] : .isButton)
    }
}

extension MeadowPaletteRow where Leading == MeadowRowIcon {
    init(_ title: String, icon: MeadowIcon?, detail: String? = nil, keys: [String] = [], picked: Bool = false,
         pop: Bool = false, danger: Bool = false, check: Bool = false) {
        self.init(title: title, detail: detail, keys: keys, picked: picked, pop: pop, danger: danger, check: check) {
            MeadowRowIcon(icon: icon)
        }
    }
}

/// A row's icon, or the room for one.
struct MeadowRowIcon: View {
    let icon: MeadowIcon?

    var body: some View {
        if let icon { MeadowIconView(icon) } else { Color.clear.frame(width: 18, height: 18) }
    }
}

/// A command's keys as the palette shows them, each on a cap of its own: "g i" is G then I, a letter is its capital,
/// and a chord ("⇧⌘M") stays one cap.
enum MeadowKeys {
    static func caps(_ hint: String?) -> [String] {
        guard let hint, !hint.trimmingCharacters(in: .whitespaces).isEmpty else { return [] }
        return hint.split(separator: " ").map { part in
            let key = String(part)
            return key.count == 1 ? key.uppercased() : key
        }
    }
}

/// The palette's foot (`.pal-ft`): what the keys do, a hint at a time, over a hairline.
struct MeadowPaletteFoot: View {
    struct Hint: Hashable {
        let keys: [String]
        let words: String
    }

    let hints: [Hint]
    /// The search's operators, on a line of their own as the mockup's foot wraps them.
    var operators: [String] = []
    @Environment(\.meadow) private var meadow

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 16) {
                ForEach(hints, id: \.self) { hint in
                    HStack(spacing: 5) {
                        ForEach(hint.keys, id: \.self) { MeadowKey($0) }
                        Text(hint.words)
                    }
                    .accessibilityElement(children: .combine)
                }
            }
            if !operators.isEmpty {
                HStack(spacing: 5) {
                    Text("Operators:")
                    ForEach(operators, id: \.self) {
                        Text(verbatim: $0).font(.meadow(.mono, 12)).foregroundStyle(meadow.ink2)
                    }
                }
                .accessibilityElement(children: .combine)
            }
        }
        .font(.meadow(.ui, 12))
        .foregroundStyle(meadow.ink3)
        .lineLimit(1)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 10)
        .padding(.horizontal, 16)
        .overlay(alignment: .top) { Rectangle().fill(meadow.line).frame(height: 1) }
    }
}

/// What a dialog holds that may grow past its room (`.pal-body`): as tall as it is, up to `maxHeight`, then scrolled.
struct MeadowFitScroll<Content: View>: View {
    let maxHeight: CGFloat
    @ViewBuilder let content: () -> Content
    @State private var height: CGFloat?

    var body: some View {
        ScrollView {
            content()
                .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { height = $0 }
        }
        .scrollBounceBehavior(.basedOnSize)
        .frame(height: min(height ?? maxHeight, maxHeight))
    }
}

/// A spinner and words in moss while the AI reads (`.thinking`); still where motion is reduced.
struct MeadowThinking: View {
    let text: String
    @Environment(\.meadow) private var meadow
    @Environment(\.accessibilityReduceMotion) private var still
    @State private var turning = false

    var body: some View {
        HStack(spacing: 10) {
            Circle()
                .trim(from: 0, to: 0.75)
                .stroke(meadow.moss, style: StrokeStyle(lineWidth: 2, lineCap: .butt))
                .frame(width: 12, height: 12)
                .padding(1)
                .rotationEffect(.degrees(turning ? 360 : 0))
                .animation(still ? nil : .linear(duration: 0.8).repeatForever(autoreverses: false), value: turning)
                .onAppear { turning = !still }
                .accessibilityHidden(true)
            Text(text)
        }
        .font(.meadow(.ui, 14, .semibold))
        .foregroundStyle(meadow.moss)
    }
}

// MARK: The popover

/// A popover of raised paper (`.pop`): radius 14, the popover's shadow, 6 around what it holds, 250 wide at least.
struct MeadowPop: ViewModifier {
    var width: CGFloat?
    @Environment(\.meadow) private var meadow

    func body(content: Content) -> some View {
        content
            .padding(6)
            .frame(minWidth: 250, idealWidth: width, maxWidth: width, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 14).fill(meadow.paperRaised))
            .clipShape(RoundedRectangle(cornerRadius: 14))
            .meadowShadow(meadow.shadowPop, radius: 14)
    }
}

extension View {
    /// Lay this on a popover's paper, `width` wide or as wide as it needs from 250.
    func meadowPop(width: CGFloat? = nil) -> some View {
        modifier(MeadowPop(width: width))
    }

    /// Hang `pop` under this view while `isPresented`, as Meadow's popovers hang (`MeadowPopoverAnchor`), lit as the
    /// window is: the panel it stands in is a window of its own, outside the window's environment.
    func meadowPopover(isPresented: Binding<Bool>, keys: @escaping (NSEvent) -> Bool = { _ in false },
                       @ViewBuilder _ pop: @escaping () -> some View) -> some View {
        modifier(MeadowPopover(isPresented: isPresented, keys: keys, pop: pop))
    }

    /// Say where this button stands in the window, under `name`, for what a key opens beside it.
    func meadowAnchor(_ name: String) -> some View {
        modifier(MeadowAnchorModifier(name: name))
    }
}

private struct MeadowPopover<Pop: View>: ViewModifier {
    @Binding var isPresented: Bool
    let keys: (NSEvent) -> Bool
    let pop: () -> Pop
    @Environment(\.meadowLight) private var light

    func body(content: Content) -> some View {
        content.background {
            MeadowPopoverAnchor(shown: $isPresented, keys: keys) {
                pop().meadowLight(light, highContrast: AppStatus.shared.highContrast)
            }
        }
    }
}

/// A popover's rule between its parts (`.pop hr`).
struct MeadowPopRule: View {
    @Environment(\.meadow) private var meadow

    var body: some View {
        Rectangle()
            .fill(meadow.line)
            .frame(height: 1)
            .padding(.vertical, 6)
            .padding(.horizontal, 4)
            .accessibilityHidden(true)
    }
}

/// One thing a popover's menu does (`.pop .pal-i`).
struct MeadowMenuItem: Identifiable {
    let id: String
    let title: String
    var detail: String?
    var icon: MeadowIcon?
    /// What stands where the icon would (an account's dot), in its place.
    var leading: AnyView?
    var keys: [String] = []
    var check = false
    var disabled = false
    /// Whether choosing it closes the popover first, as nearly every item does.
    var closes = true
    let run: @MainActor () -> Void
}

/// The keys of a popover's menu while it is open: ↑ and ↓ go from item to item, Return runs the one they are on, and
/// what the menu takes for itself (a snooze's digits) comes to `onKey` first. The popover's panel is never the key
/// window, so its keys come here through `MeadowPopoverAnchor`'s `keys`, and the item they are on is ringed.
@MainActor
@Observable
final class MeadowMenuModel {
    /// The item the keys are on: the first as the menu opens, as the mockup's popover focuses it.
    var at = 0
    @ObservationIgnored var items: [MeadowMenuItem] = []
    @ObservationIgnored var onKey: ((NSEvent) -> Bool)?
    @ObservationIgnored var close: @MainActor () -> Void = {}

    /// Say whether the key was the menu's: never one with ⌘, ⌃ or ⌥ held, as `MeadowPopoverKeys` has it.
    func key(_ event: NSEvent) -> Bool {
        guard MeadowPopoverKeys.plain(event.modifierFlags) else { return false }
        if let onKey, onKey(event) { return true }
        let items = items.filter { !$0.disabled }
        guard !items.isEmpty else { return false }
        let at = min(self.at, items.count - 1)
        switch event.keyCode {
        case 125: self.at = (at + 1) % items.count
        case 126: self.at = (at - 1 + items.count) % items.count
        case 36, 76: run(items[at])
        default: return false
        }
        return true
    }

    /// Close first, so what the item opens gets the focus.
    func run(_ item: MeadowMenuItem) {
        guard !item.disabled else { return }
        if item.closes { close() }
        item.run()
    }
}

/// A popover's menu (`popover()`): its parts in order, the item the keys are on ringed, the one the pointer is over a
/// step darker, a click running it. `model` holds its keys.
struct MeadowMenu: View {
    enum Part {
        case title(String)
        case rule
        case item(MeadowMenuItem)
        /// Items that may be many, scrolling past `maxHeight`: the keys go over them as over the rest, the scroll
        /// following the one they are on, and a click runs one and closes the menu as any item does.
        case scroll([MeadowMenuItem], maxHeight: CGFloat)
        case view(AnyView)
    }

    let parts: [Part]
    var width: CGFloat?
    let model: MeadowMenuModel
    let close: @MainActor () -> Void
    var label = ""
    /// Whether each item keeps room above and below it for the focus ring, which reaches past its row: where an item
    /// with a second line sits under another, its title is at its top, and the ring on the one above would cross it.
    var ringRoom = false

    /// How far the focus ring reaches past its row: drawn three points out, half its two-point stroke further.
    static let ringReach: CGFloat = 4

    /// The items the keys move over, in order, those in a scroll among them.
    static func items(_ parts: [Part]) -> [MeadowMenuItem] {
        parts.flatMap { part -> [MeadowMenuItem] in
            switch part {
            case .item(let item): [item]
            case .scroll(let items, _): items
            case .title, .rule, .view: []
            }
        }
        .filter { !$0.disabled }
    }

    var body: some View {
        let items = Self.items(parts)
        let at = items.isEmpty ? nil : items[min(model.at, items.count - 1)].id
        VStack(alignment: .leading, spacing: 0) {
            ForEach(Array(parts.enumerated()), id: \.offset) { _, part in
                switch part {
                case .title(let title): MeadowPaletteSection(title, pop: true)
                case .rule: MeadowPopRule()
                case .view(let view): view
                case .item(let item): row(item, at: at)
                case .scroll(let scrolled, let maxHeight):
                    ScrollViewReader { scroll in
                        MeadowFitScroll(maxHeight: maxHeight) {
                            VStack(spacing: 0) {
                                ForEach(scrolled) { item in row(item, at: at).id(item.id) }
                            }
                        }
                        .onChange(of: at) { _, now in
                            if let now, scrolled.contains(where: { $0.id == now }) { scroll.scrollTo(now) }
                        }
                    }
                }
            }
        }
        .meadowPop(width: width)
        .onAppear { offer(items) }
        .onChange(of: items.map { "\($0.id)\u{0}\($0.disabled)" }) { offer(items) }
        .accessibilityElement(children: .contain)
        .accessibilityLabel(label)
    }

    /// An item's row: ringed while the keys are on it, run by a click or by a screen reader's press.
    private func row(_ item: MeadowMenuItem, at: String?) -> some View {
        MeadowPaletteRow(title: item.title, detail: item.detail, keys: item.keys, picked: item.id == at,
                         pop: true, check: item.check) {
            if let leading = item.leading { leading } else if let icon = item.icon { MeadowIconView(icon) }
        }
        .padding(.vertical, ringRoom ? Self.ringReach / 2 : 0)
        .disabled(item.disabled)
        .onTapGesture { model.run(item) }
        .accessibilityAction { model.run(item) }
    }

    private func offer(_ items: [MeadowMenuItem]) {
        model.items = items
        model.close = close
    }
}

/// How one thing stands (`.health`): its dot, its name and a quieter line under it, and how it is on the right.
struct MeadowHealthRow: View {
    let dot: Color
    let title: String
    var detail: String?
    let state: String
    var stateColor: Color?
    @Environment(\.meadow) private var meadow

    var body: some View {
        HStack(spacing: 10) {
            Circle()
                .fill(dot)
                .frame(width: 8, height: 8)
                .background { Circle().fill(dot.opacity(0.22)).frame(width: 14, height: 14) }
                .frame(width: 10)
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(.meadow(.ui, 13, .bold)).foregroundStyle(meadow.ink)
                if let detail, !detail.isEmpty {
                    Text(detail).font(.meadow(.ui, 12)).foregroundStyle(meadow.ink3)
                }
            }
            .lineLimit(1)
            Spacer(minLength: 10)
            Text(state)
                .font(.meadow(.ui, 12))
                .foregroundStyle(stateColor ?? meadow.ink3)
                .lineLimit(1)
        }
        .padding(.vertical, 8)
        .padding(.horizontal, 10)
        .accessibilityElement(children: .combine)
    }
}

/// Where a popover stands beside the button it came from, in a window of `size` with its origin at the top left
/// (`popover()`): under it, 6 below, its left edge on the button's; above it where it wouldn't fit below; never
/// nearer an edge than 12.
enum MeadowPopPlacement {
    static func origin(of pop: CGSize, under anchor: CGRect?, in size: CGSize) -> CGPoint {
        // With no button on screen, near the middle: the mockup's for a key's chooser.
        let anchor = anchor ?? CGRect(x: size.width / 2 - 130, y: size.height / 3, width: 0, height: 0)
        let x = max(12, min(size.width - pop.width - 12, anchor.minX))
        let y = anchor.maxY + pop.height + 8 > size.height ? max(12, anchor.minY - pop.height - 8) : anchor.maxY + 6
        return CGPoint(x: x, y: y)
    }
}

/// A popover of Meadow's own (`.pop`), hung under the view it is the background of: a borderless panel over the
/// window, so it isn't clipped by the panel it opens from, and drawn whole by its content (its paper, its corners,
/// its shadow), placed as `MeadowPopPlacement` says. A click anywhere else or Esc closes it; `keys` takes the keys it
/// wants while it is open.
struct MeadowPopoverAnchor<Content: View>: NSViewRepresentable {
    @Binding var shown: Bool
    var keys: (NSEvent) -> Bool = { _ in false }
    @ViewBuilder let content: () -> Content

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> MeadowPopoverAnchorView { MeadowPopoverAnchorView() }

    func updateNSView(_ view: MeadowPopoverAnchorView, context: Context) {
        let coordinator = context.coordinator
        coordinator.close = { shown = false }
        coordinator.keys = keys
        if shown {
            coordinator.show(content(), under: view)
        } else {
            coordinator.hide()
        }
    }

    static func dismantleNSView(_ view: MeadowPopoverAnchorView, coordinator: Coordinator) {
        coordinator.hide()
    }

    @MainActor
    final class Coordinator {
        /// Room around the content for its shadow to fall in.
        static var room: CGFloat { 28 }
        var close: () -> Void = {}
        var keys: (NSEvent) -> Bool = { _ in false }
        private var panel: NSPanel?
        private var host: NSHostingView<AnyView>?
        private var monitor: Any?
        /// What it hangs from: the button that opened it.
        private weak var anchor: NSView?
        /// What it shows while it is asked to: kept until its anchor is in a window to hang it in.
        private var root: AnyView?

        /// Whether the panel is up over a window.
        var isShown: Bool { panel?.parent != nil }

        /// Show `content` under `anchor`. An anchor made in the same update that asks for the popover (the snooze
        /// chooser's, as `h` opens it) isn't in a window yet: the popover shows as the anchor joins one.
        func show(_ content: Content, under anchor: NSView) {
            self.anchor = anchor
            let root = AnyView(content.padding(Self.room))
            self.root = root
            host?.rootView = root
            (anchor as? MeadowPopoverAnchorView)?.moved = { [weak self] in self?.place() }
            place()
        }

        /// Hang the panel at its anchor, or at its anchor's new place, once the anchor is in a window.
        func place() {
            guard let root, let anchor, let window = anchor.window else { return }
            let panel = panel ?? made(root)
            let size = host?.fittingSize ?? .zero
            let pop = CGSize(width: max(0, size.width - 2 * Self.room), height: max(0, size.height - 2 * Self.room))
            // The button in the window, its origin at the top left as the placement has it.
            let space = window.frame.size
            let button = anchor.convert(anchor.bounds, to: nil)
            let under = CGRect(x: button.minX, y: space.height - button.maxY, width: button.width,
                               height: button.height)
            let at = MeadowPopPlacement.origin(of: pop, under: under, in: space)
            let frame = NSRect(x: window.frame.minX + at.x - Self.room,
                               y: window.frame.minY + space.height - at.y - pop.height - Self.room,
                               width: size.width, height: size.height)
            // Its paper stands 12 in from the window's edge, its shadow's room wider: the panel is cut at the edge,
            // so nothing of it falls past the window, as nothing of a toast does.
            let shown = frame.intersection(window.frame)
            panel.setFrame(shown.isEmpty ? frame : shown, display: true)
            host?.frame = NSRect(x: frame.minX - panel.frame.minX, y: frame.minY - panel.frame.minY,
                                 width: frame.width, height: frame.height)
            if panel.parent == nil {
                window.addChildWindow(panel, ordered: .above)
                panel.orderFront(nil)
                watch(panel)
            }
        }

        func hide() {
            root = nil
            if let monitor { NSEvent.removeMonitor(monitor) }
            monitor = nil
            if let panel {
                panel.parent?.removeChildWindow(panel)
                panel.orderOut(nil)
            }
        }

        private func made(_ root: AnyView) -> NSPanel {
            let panel = NSPanel(contentRect: .zero, styleMask: [.borderless, .nonactivatingPanel], backing: .buffered,
                                defer: true)
            panel.isOpaque = false
            panel.backgroundColor = .clear
            panel.hasShadow = false
            panel.isReleasedWhenClosed = false
            panel.becomesKeyOnlyIfNeeded = true
            let host = NSHostingView(rootView: root)
            host.sizingOptions = [.intrinsicContentSize]
            // The content in a plain view that holds it whole where the panel is cut at the window's edge.
            let holder = NSView()
            holder.addSubview(host)
            panel.contentView = holder
            self.panel = panel
            self.host = host
            return panel
        }

        /// A key typed while it is up, and what goes on of it: Esc closes it; a key it may take is its own, taken or
        /// not, as an open menu holds the keyboard, so the list behind never acts on it (j would move the list's choice
        /// under the snooze chooser, and its 1 would then snooze another message); any other goes on untouched.
        func typed(_ event: NSEvent) -> NSEvent? {
            switch MeadowPopoverKeys.verdict(keyCode: event.keyCode, modifiers: event.modifierFlags,
                                             typedIn: event.window, hungIn: panel?.parent, panel: panel) {
            case .close:
                close()
                return nil
            case .offer:
                _ = keys(event)
                return nil
            case .pass:
                return event
            }
        }

        /// Close on a click outside the menu and on Esc, and hand it the other keys typed in the window it hangs in, as
        /// `MeadowPopoverKeys` says.
        private func watch(_ panel: NSPanel) {
            monitor = NSEvent.addLocalMonitorForEvents(matching: [.leftMouseDown, .rightMouseDown, .keyDown]) {
                [weak self, weak panel] event in
                guard let self else { return event }
                if event.type == .keyDown { return self.typed(event) }
                guard event.window !== panel else { return event }
                self.close()
                // A press on the button it hangs from only closes it: the button's own press would open it again.
                let again = MeadowPopoverKeys.presses(self.anchor, at: event.locationInWindow, in: event.window)
                return again ? nil : event
            }
        }
    }
}

/// What a popover hangs from: it says when it joins a window or moves in one, so a popover asked for before its anchor
/// was in a window shows as it arrives, and one that is up follows it.
final class MeadowPopoverAnchorView: NSView {
    var moved: (() -> Void)?

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        moved?()
    }

    override func setFrameOrigin(_ newOrigin: NSPoint) {
        let was = frame.origin
        super.setFrameOrigin(newOrigin)
        if newOrigin != was, window != nil { moved?() }
    }

    override func setFrameSize(_ newSize: NSSize) {
        let was = frame.size
        super.setFrameSize(newSize)
        if newSize != was, window != nil { moved?() }
    }
}

/// Which keys typed while a Meadow popover is open are its own. Its panel is never the key window, so it hears the
/// keys through a monitor of the whole app's: it takes only those typed in the window it hangs in (or its own panel),
/// none while that window's keyboard is in something that takes text, whose keys are that thing's, and none with ⌘,
/// ⌃ or ⌥ held, which are the menus' and the window's (⌘1 goes to Inbox, ⌘Return sends). A click on the button it
/// hangs from closes it, and goes no further.
@MainActor
enum MeadowPopoverKeys {
    enum Verdict: Equatable {
        /// Esc: the popover closes.
        case close
        /// The popover's keys may take it, and it goes no further.
        case offer
        /// Not the popover's: it goes on untouched.
        case pass
    }

    static func verdict(keyCode: UInt16, modifiers: NSEvent.ModifierFlags, typedIn window: NSWindow?,
                        hungIn parent: NSWindow?, panel: NSWindow?) -> Verdict {
        guard let window, window === parent || window === panel, plain(modifiers) else { return .pass }
        // Esc closes it even from a field: what is typed there goes on once the popover is gone.
        if keyCode == 53 { return .close }
        return window !== panel && typesText(window.firstResponder) ? .pass : .offer
    }

    /// Whether a click at `point` in `window` lands on `anchor`, the button a popover hangs from.
    static func presses(_ anchor: NSView?, at point: NSPoint, in window: NSWindow?) -> Bool {
        guard let anchor, let window, anchor.window === window else { return false }
        return anchor.convert(anchor.bounds, to: nil).contains(point)
    }

    /// Whether a key is typed with nothing held that makes it a command's: ⇧ makes another character, not a command.
    static func plain(_ modifiers: NSEvent.ModifierFlags) -> Bool {
        modifiers.isDisjoint(with: [.command, .control, .option])
    }

    /// Whether `responder` takes typed text: a field's editor while a field is edited, a text view that can be, or a
    /// date and time typed in a field of their own (a snooze's time of one's own).
    static func typesText(_ responder: NSResponder?) -> Bool {
        if let picker = responder as? NSDatePicker { return picker.datePickerStyle != .clockAndCalendar }
        return (responder as? NSTextView)?.isEditable ?? false
    }
}

/// Where named buttons stand in the window (the pane's Snooze), for what a key opens beside them: a popover hung at
/// the place the button is.
@MainActor
final class MeadowAnchors {
    private(set) var frames: [String: CGRect] = [:]
    /// Which view said where each name stands last, so one that goes forgets only its own: the pane's bar draws
    /// its Snooze again in each fold, and the fold that goes leaves after the one that comes has said where it is.
    private var by: [String: UUID] = [:]

    func remember(_ name: String, at frame: CGRect, by view: UUID? = nil) {
        frames[name] = frame
        by[name] = view
    }

    /// Forget where `name` stands: what any view said, or only what `view` said last.
    func forget(_ name: String, by view: UUID? = nil) {
        guard view == nil || by[name] == view else { return }
        frames[name] = nil
        by[name] = nil
    }

    /// The list's: where the clock of a message's row stands, while the list shows it.
    var rowClock: (@MainActor (MailCopy) -> CGRect?)?

    /// Where the snooze chooser for `copies` hangs, as a click on what offers it hangs it: under the pane's Snooze for
    /// the message in the pane, else under the Snooze of the brief's card for the one message, else under the clock of
    /// the first of their rows the list shows (the rows marked), else under the pane's Snooze; nil where none is on
    /// screen.
    func snooze(for copies: [MailCopy], open: MailCopy?) -> CGRect? {
        if let open, copies == [open], let pane = frames["snooze"] { return pane }
        if copies.count == 1, let card = frames[Self.cardSnooze(copies[0])] { return card }
        if let clock = copies.lazy.compactMap({ self.rowClock?($0) }).first { return clock }
        return frames["snooze"]
    }

    /// The name a brief card's Snooze says where it stands by, for its message.
    static func cardSnooze(_ copy: MailCopy) -> String {
        "snooze-card \(copy.account)\n\(copy.folder)\n\(copy.uid)"
    }
}

extension EnvironmentValues {
    /// The window's buttons that something may be opened beside; none where a view is drawn away from a window's.
    @Entry var meadowAnchors: MeadowAnchors?
}

private struct MeadowAnchorModifier: ViewModifier {
    let name: String
    @Environment(\.meadowAnchors) private var anchors
    @State private var view = UUID()

    func body(content: Content) -> some View {
        content
            .onGeometryChange(for: CGRect.self) { $0.frame(in: .global) } action: { frame in
                anchors?.remember(name, at: frame, by: view)
            }
            .onDisappear { anchors?.forget(name, by: view) }
    }
}

// MARK: The toast

/// A moment's word at the window's foot (`.toast`): dark ink on Day's and Dusk's paper, paper on Night's, its icon,
/// its words, and a button or two on a wash of its own (Undo, with its key).
struct MeadowToast<Actions: View>: View {
    var icon: MeadowIcon = .check
    let text: Text
    var detail: String?
    @ViewBuilder let actions: () -> Actions
    @Environment(\.meadowLight) private var light

    var body: some View {
        let colours = MeadowToastColours(light)
        HStack(spacing: 12) {
            MeadowIconView(icon)
                .opacity(0.8)
            VStack(alignment: .leading, spacing: 1) {
                text
                if let detail, !detail.isEmpty {
                    Text(detail).font(.meadow(.ui, 12.5)).opacity(0.75)
                }
            }
            .lineLimit(2)
            .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 6) { actions() }
        }
        .font(.meadow(.ui, 14, .semibold))
        .foregroundStyle(colours.ink)
        .padding(.vertical, 5)
        .padding(.leading, 16)
        .padding(.trailing, 5)
        .frame(minHeight: 44)
        .background(RoundedRectangle(cornerRadius: 14).fill(colours.fill))
        .meadowShadow([MeadowShadow(x: 0, y: 18, blur: 40, spread: -18, color: .black.opacity(0.6), inset: false)],
                      radius: 14)
        .environment(\.meadowToast, colours)
        .accessibilityElement(children: .contain)
    }
}

extension MeadowToast where Actions == EmptyView {
    init(icon: MeadowIcon = .check, _ text: String, detail: String? = nil) {
        self.init(icon: icon, text: Text(text), detail: detail) { EmptyView() }
    }

    /// Words said for a moment, with the alert for a refusal or a failure and the tick for the rest.
    init(_ words: ToastWords) {
        self.init(icon: words.icon, words.text)
    }
}

/// A few words a toast says, and whether they turn something down: a refusal or a failure never shows the tick that
/// says something was done.
struct ToastWords: Equatable {
    let text: String
    var refused = false

    var icon: MeadowIcon {
        refused || MailboxToast.icon(text) == .alert ? .alert : .check
    }
}

/// A toast's colours: ink and paper the other way about, and what its buttons lie on.
struct MeadowToastColours: Equatable {
    let fill: Color
    let ink: Color
    let button: Color

    init(_ light: MeadowLight) {
        if light.isDark {
            (fill, ink, button) = (Color(meadow: 0xEDE7D7), Color(meadow: 0x1B2238), .black.opacity(0.08))
        } else {
            (fill, ink, button) = (Color(meadow: 0x24221E), Color(meadow: 0xF5EFE2), .white.opacity(0.12))
        }
    }
}

extension EnvironmentValues {
    /// Inside a toast, whose buttons and keys take its colours.
    @Entry var meadowToast: MeadowToastColours?
}

/// A toast's button (`.toast button`): its words heavy on a wash of the toast's ink, 34 high, with an icon and a key.
struct MeadowToastButton: View {
    let title: String
    var icon: MeadowIcon?
    var key: String?
    let action: () -> Void
    @Environment(\.meadowToast) private var colours
    @Environment(\.meadowLight) private var light
    @FocusState private var focused: Bool

    var body: some View {
        let colours = colours ?? MeadowToastColours(light)
        Button(action: action) {
            HStack(spacing: 8) {
                if let icon { MeadowIconView(icon, size: 15) }
                Text(title).fixedSize()
                if let key {
                    Text(verbatim: key)
                        .font(.meadow(.mono, 10.5, .bold))
                        .padding(.horizontal, 4)
                        .frame(minWidth: 19, minHeight: 19)
                        .overlay {
                            RoundedRectangle(cornerRadius: 5).strokeBorder(colours.ink, lineWidth: 1)
                        }
                        .opacity(0.6)
                }
            }
            .font(.meadow(.ui, 14, .heavy))
            .foregroundStyle(colours.ink)
            .padding(.horizontal, 12)
            .frame(minHeight: 34)
            .background(RoundedRectangle(cornerRadius: 10).fill(colours.button))
            .contentShape(RoundedRectangle(cornerRadius: 10))
        }
        .buttonStyle(.plain)
        .fixedSize()
        .focused($focused)
        .focusEffectDisabled()
        .meadowFocusRing(focused, radius: 10)
        .accessibilityLabel(Text(title))
    }
}
#endif
