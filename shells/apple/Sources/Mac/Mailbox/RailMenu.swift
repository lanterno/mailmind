#if os(macOS)
import AppKit
import SwiftUI

// The accounts' button at the top of the rail and the menu it opens (#44), as the mockup's `.acct-btn` and
// `acctMenu()` draw them: whose mail the window shows, every account's or one's, and adding another.

/// What the accounts' menu offers.
enum RailMenuChoice: Hashable {
    case all
    case account(String)
    case add
}

/// The accounts' button (`.acct-btn`): their colours stacked, whose mail the window shows and under it how many or
/// the address, and a chevron; folded, only the colours. It opens the menu, and the rail with it.
struct RailAccountButton: View {
    let mailbox: Mailbox
    let place: Place?
    let open: Bool
    @Binding var menuOpen: Bool
    let unfold: () -> Void
    @Environment(\.meadow) private var meadow
    @Environment(\.meadowLight) private var light
    @Environment(\.openSettings) private var openSettings
    @State private var menu = RailMenuModel()
    @State private var hovering = false
    @FocusState private var focused: Bool

    var body: some View {
        let account = place?.account
        Button {
            if !open { unfold() }
            menuOpen.toggle()
        } label: {
            HStack(spacing: 10) {
                RailAccountStack(colours: colours(of: account))
                if open {
                    // Each line as tall as the page's 1.45 makes it.
                    VStack(alignment: .leading, spacing: 0) {
                        Text(account.map { mailbox.name(of: $0) } ?? String(localized: "All accounts"))
                            .font(.meadow(.ui, 13.5, .bold))
                            .foregroundStyle(meadow.ink)
                            .frame(height: 13.5 * 1.45)
                        Text(account ?? RailMenuModel.together(mailbox.accounts.count))
                            .font(.meadow(.ui, 12))
                            .foregroundStyle(meadow.ink3)
                            .frame(height: 12 * 1.45)
                    }
                    .lineLimit(1)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    MeadowIconView(.down, size: 15).foregroundStyle(meadow.ink2)
                }
            }
            // The padding and the border's point inside it, as CSS's border-box has them.
            .padding(.horizontal, open ? 11 : 0)
            .padding(.vertical, 7)
            .frame(maxWidth: .infinity, minHeight: 44)
            .background(RoundedRectangle(cornerRadius: 12).fill(meadow.paperRaised))
            .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(hovering ? meadow.line2 : meadow.line))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .focused($focused)
        .focusEffectDisabled()
        .meadowFocusRing(focused, radius: 12)
        .onHover { hovering = $0 }
        .background {
            MeadowPopoverAnchor(shown: $menuOpen, keys: { menu.key($0) }) {
                // The panel is a window of its own, outside the frame's environment: it is lit as the frame is.
                RailAccountMenu(model: menu)
                    .meadowLight(light, highContrast: AppStatus.shared.preferences?.highContrast ?? false)
            }
        }
        .onChange(of: menuOpen, initial: true) { _, shown in
            guard shown else { return }
            menu.offer(mailbox: mailbox, current: account, colours: { colours(of: $0) }) { choice in
                menuOpen = false
                choose(choice)
            }
        }
        .accessibilityLabel("Accounts")
        .accessibilityValue(Text(account.map { mailbox.name(of: $0) } ?? String(localized: "All accounts")))
        .accessibilityHint("Shows mail from every account or from one")
    }

    /// The colours stacked: every account's while the window shows them all, else the one's.
    private func colours(of account: String?) -> [Color] {
        let accounts = account.map { [$0] } ?? mailbox.accounts
        return accounts.prefix(4).map { meadow.account(mailbox.accounts.firstIndex(of: $0) ?? 0) }
    }

    private func choose(_ choice: RailMenuChoice) {
        switch choice {
        case .all:
            mailbox.app?.place = .allInboxes
        case .account(let account):
            let inbox = mailbox.folders(of: account).first { $0.role == "inbox" }?.name ?? "INBOX"
            mailbox.app?.place = .folder(account: account, folder: inbox)
        case .add:
            AppStatus.shared.settingsTab = .accounts
            openSettings()
        }
    }
}

/// The accounts' colours, each a dot ringed in raised paper, overlapping (`.acct-stack`).
struct RailAccountStack: View {
    let colours: [Color]
    @Environment(\.meadow) private var meadow

    var body: some View {
        HStack(spacing: -5) {
            ForEach(Array(colours.enumerated()), id: \.offset) { _, colour in
                Circle()
                    .fill(colour)
                    .overlay { Circle().strokeBorder(meadow.paperRaised, lineWidth: 2) }
                    .frame(width: 13, height: 13)
            }
        }
        .accessibilityHidden(true)
    }
}

/// The menu's state: what it offers, the line the keyboard is on, and what choosing does.
@MainActor
@Observable
final class RailMenuModel {
    struct Line: Identifiable {
        let choice: RailMenuChoice
        let title: String
        let detail: String?
        let colours: [Color]
        let chosen: Bool
        var id: RailMenuChoice { choice }
    }

    private(set) var lines: [Line] = []
    var at: Int?
    @ObservationIgnored private var chose: (RailMenuChoice) -> Void = { _ in }

    /// "3 accounts, one inbox", or for one account only "1 account".
    static func together(_ count: Int) -> String {
        count == 1 ? String(localized: "1 account") : String(localized: "\(count) accounts, one inbox")
    }

    func offer(mailbox: Mailbox, current: String?, colours: (String?) -> [Color],
               choose: @escaping (RailMenuChoice) -> Void) {
        var lines = [Line(choice: .all, title: String(localized: "All accounts"),
                          detail: String(localized: "one merged inbox"), colours: colours(nil), chosen: current == nil)]
        for account in mailbox.accounts {
            lines.append(Line(choice: .account(account), title: mailbox.name(of: account), detail: account,
                              colours: colours(account), chosen: current == account))
        }
        lines.append(Line(choice: .add, title: String(localized: "Add an account"), detail: nil, colours: [],
                          chosen: false))
        self.lines = lines
        at = nil
        chose = choose
    }

    func choose(_ choice: RailMenuChoice) {
        chose(choice)
    }

    /// The keys while the menu is open: the arrows go from line to line, Return chooses, Esc closes. Says whether the
    /// key was the menu's.
    func key(_ event: NSEvent) -> Bool {
        guard !lines.isEmpty else { return false }
        switch event.keyCode {
        case 125:
            at = at.map { min($0 + 1, lines.count - 1) } ?? 0
        case 126:
            at = at.map { max($0 - 1, 0) } ?? lines.count - 1
        case 36, 76:
            if let at, lines.indices.contains(at) { choose(lines[at].choice) }
        default:
            return false
        }
        return true
    }
}

/// The menu (`.pop`): "Show mail from", every account together, each one, and adding another.
struct RailAccountMenu: View {
    let model: RailMenuModel
    @Environment(\.meadow) private var meadow

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            MeadowSectionLabel(String(localized: "Show mail from"))
                .padding(EdgeInsets(top: 8, leading: 10, bottom: 6, trailing: 10))
            ForEach(Array(model.lines.enumerated()), id: \.element.id) { index, line in
                if line.choice == .add {
                    Rectangle().fill(meadow.line).frame(height: 1).padding(.vertical, 6).padding(.horizontal, 4)
                }
                Button { model.choose(line.choice) } label: { item(line, at: index) }
                    .buttonStyle(.plain)
                    .onHover { if $0 { model.at = index } }
                    .accessibilityAddTraits(line.chosen ? .isSelected : [])
            }
        }
        .padding(6)
        .frame(minWidth: 250, alignment: .leading)
        .fixedSize()
        .meadowPanel(radius: 14, raised: true)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Show mail from")
    }

    private func item(_ line: RailMenuModel.Line, at index: Int) -> some View {
        HStack(spacing: 12) {
            if line.choice == .add {
                MeadowIconView(.plus).foregroundStyle(meadow.ink3)
            } else if line.colours.count > 1 {
                RailAccountStack(colours: line.colours)
            } else {
                Circle().fill(line.colours.first ?? meadow.moss).frame(width: 8, height: 8)
            }
            VStack(alignment: .leading, spacing: 0) {
                Text(line.title).font(.meadow(.ui, 14)).foregroundStyle(meadow.ink)
                if let detail = line.detail {
                    Text(detail).font(.meadow(.ui, 12.5)).foregroundStyle(meadow.ink3)
                }
            }
            .lineLimit(1)
            Spacer(minLength: 12)
            if line.chosen {
                MeadowIconView(.check).foregroundStyle(meadow.ink3)
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 4)
        .frame(minHeight: 38)
        .background(RoundedRectangle(cornerRadius: 10).fill(model.at == index ? meadow.paper3 : .clear))
        .contentShape(Rectangle())
    }
}

/// A popover of Meadow's own (`.pop`), hung under the view it is the background of: a borderless panel over the
/// window, so it isn't clipped by the panel it opens from, and drawn whole by its content (its paper, its corners,
/// its shadow). A click anywhere else or Esc closes it; `keys` takes the keys it wants while it is open.
struct MeadowPopoverAnchor<Content: View>: NSViewRepresentable {
    @Binding var shown: Bool
    var keys: (NSEvent) -> Bool = { _ in false }
    @ViewBuilder let content: () -> Content

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> NSView { NSView() }

    func updateNSView(_ view: NSView, context: Context) {
        let coordinator = context.coordinator
        coordinator.close = { shown = false }
        coordinator.keys = keys
        if shown {
            coordinator.show(content(), under: view)
        } else {
            coordinator.hide()
        }
    }

    static func dismantleNSView(_ view: NSView, coordinator: Coordinator) {
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

        func show(_ content: Content, under anchor: NSView) {
            guard let window = anchor.window else { return }
            let root = AnyView(content.padding(Self.room))
            let panel = panel ?? made(root)
            host?.rootView = root
            let size = host?.fittingSize ?? .zero
            let below = window.convertToScreen(anchor.convert(anchor.bounds, to: nil))
            let frame = NSRect(x: below.minX - Self.room, y: below.minY - 6 - size.height + Self.room,
                               width: size.width, height: size.height)
            panel.setFrame(frame, display: true)
            if panel.parent == nil {
                window.addChildWindow(panel, ordered: .above)
                panel.orderFront(nil)
                watch(panel)
            }
        }

        func hide() {
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
            panel.contentView = host
            self.panel = panel
            self.host = host
            return panel
        }

        /// Close on a click outside the menu and on Esc, and hand it the other keys it wants.
        private func watch(_ panel: NSPanel) {
            monitor = NSEvent.addLocalMonitorForEvents(matching: [.leftMouseDown, .rightMouseDown, .keyDown]) {
                [weak self, weak panel] event in
                guard let self else { return event }
                if event.type == .keyDown {
                    if event.keyCode == 53 {
                        self.close()
                        return nil
                    }
                    return self.keys(event) ? nil : event
                }
                if event.window !== panel { self.close() }
                return event
            }
        }
    }
}
#endif
