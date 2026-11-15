#if os(macOS)
import SwiftUI

/// The rail (#44), as the mockup's `.rail` draws it and with the places the page's rail offers (app/FolderRail.tsx):
/// the accounts' button, then every place `RailPlaces` lists, then Help and Settings at its foot. Open, each place has
/// its icon, its name and its count; folded to icons (the frame's `meadowRailOpen`), the counts that need you stay as
/// badges and the folders wait behind one button. The arrow keys go from place to place while the rail has the focus.
struct MailboxSidebar: View {
    @Bindable var app: MailApp
    let mailbox: Mailbox
    @Environment(\.meadow) private var meadow
    @Environment(\.meadowRailOpen) private var open
    @Environment(Desk.self) private var desk: Desk?
    @Environment(MeadowShell.self) private var shell: MeadowShell?
    @Environment(\.openSettings) private var openSettings
    @Environment(\.openWindow) private var openWindow
    /// The accounts whose folders are folded away, by address.
    @State private var closed: Set<String> = []
    /// The accounts whose labels are shown.
    @State private var labelsOpen: Set<String> = []
    @State private var menuOpen = false
    @FocusState private var keyed: Bool

    var body: some View {
        let entries = RailPlaces.entries(mailbox, desk: desk?.rail ?? DeskRail(), closed: closed,
                                         labelsOpen: labelsOpen)
        let shown = entries.filter { $0.shows(open: open) }
        let current = RailPlaces.current(app.place, in: entries, open: open)
        VStack(spacing: 0) {
            ScrollViewReader { scroller in
                ScrollView {
                    VStack(alignment: .leading, spacing: 0) {
                        RailAccountButton(mailbox: mailbox, place: app.place, open: open, menuOpen: $menuOpen,
                                          unfold: unfold)
                        Color.clear.frame(height: 8)
                        ForEach(shown) { entry in
                            line(entry, current: current)
                                .id(entry.id)
                        }
                    }
                    .padding(.vertical, 10)
                    .padding(.horizontal, 8)
                }
                .scrollIndicators(.automatic)
                .focusable()
                .focused($keyed)
                .focusEffectDisabled()
                .onKeyPress(.downArrow) { step(1, entries: entries, scroller: scroller) }
                .onKeyPress(.upArrow) { step(-1, entries: entries, scroller: scroller) }
            }
            foot
        }
        .task(id: desk == nil) {
            // The Inbox counts what the brief says needs you, so its tallies are read once the rail shows.
            if let desk, !desk.brief.tallied { await desk.brief.refresh() }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Mailboxes")
    }

    @ViewBuilder private func line(_ entry: RailEntry, current: String?) -> some View {
        switch entry.kind {
        case .item(let item):
            RailPlace(item: item, open: open, current: current == entry.id, keyed: keyed && current == entry.id) {
                app.place = item.place
            }
            .contextMenu {
                if let view = item.removable {
                    Button("Remove View", role: .destructive) { mailbox.removeView(view) }
                }
            }
        case .head(let title):
            MeadowSectionLabel(title)
                .padding(EdgeInsets(top: 16, leading: 10, bottom: 6, trailing: 10))
        case .account(let account):
            RailAccountName(name: mailbox.name(of: account), address: account,
                            colour: meadow.account(mailbox.accounts.firstIndex(of: account) ?? 0),
                            folded: closed.contains(account)) {
                if closed.contains(account) { closed.remove(account) } else { closed.insert(account) }
            }
        case .labels(let account, let shown):
            RailFold(title: String(localized: "Labels"), icon: .tag, open: shown) {
                if shown { labelsOpen.remove(account) } else { labelsOpen.insert(account) }
            }
        case .separator:
            Rectangle().fill(meadow.line).frame(height: 1).padding(.vertical, 8).padding(.horizontal, 6)
                .accessibilityHidden(true)
        case .folders:
            RailPlace(item: RailItem(place: .allInboxes, icon: .folder, title: String(localized: "Folders")),
                      open: false, current: current == entry.id, keyed: keyed && current == entry.id, action: unfold)
        }
    }

    /// The foot (`.rail-foot`): Help, and Settings; in a column of icons while the rail is folded.
    private var foot: some View {
        let layout = open ? AnyLayout(HStackLayout(spacing: 4)) : AnyLayout(VStackLayout(spacing: 4))
        return layout {
            Button { openWindow(id: HelpWindow.id) } label: {
                MeadowIconView(.book)
                if open {
                    Text("Help").frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .meadowButton(.quiet, icon: !open)
            .frame(width: open ? nil : 44)
            .help("MailMind Help")
            .accessibilityLabel("Help")
            Button { openSettings() } label: { MeadowIconView(.sliders) }
                .meadowButton(.quiet, icon: true)
                .frame(width: open ? nil : 44)
                .help("Settings (⌘,)")
                .accessibilityLabel("Settings")
        }
        .padding(8)
        .frame(maxWidth: .infinity)
        .overlay(alignment: .top) { Rectangle().fill(meadow.line).frame(height: 1) }
    }

    /// Show the rail's names: the folded rail's Folders button and its accounts' button open it.
    private func unfold() {
        shell?.railOpen = true
    }

    /// The next or the previous place the rail shows, chosen, as a Mac's sidebar goes by its arrow keys.
    private func step(_ by: Int, entries: [RailEntry], scroller: ScrollViewProxy) -> KeyPress.Result {
        let places = RailPlaces.places(entries, open: open)
        guard !places.isEmpty else { return .ignored }
        let at = app.place.flatMap { places.firstIndex(of: $0) }
        let next = at.map { min(max($0 + by, 0), places.count - 1) } ?? (by > 0 ? 0 : places.count - 1)
        app.place = places[next]
        if let id = RailPlaces.current(places[next], in: entries, open: open) { scroller.scrollTo(id) }
        return .handled
    }
}

/// A place in the rail (`.nav-i`): its icon, its name and its count, 36 tall, the current one in the primary's wash;
/// folded, only the icon, 44 tall, with a badge for a count that needs you.
private struct RailPlace: View {
    let item: RailItem
    let open: Bool
    let current: Bool
    /// The rail has the keyboard's focus, on this place.
    let keyed: Bool
    let action: () -> Void
    @Environment(\.meadow) private var meadow
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 11) {
                MeadowIconView(item.icon)
                if open {
                    Text(item.title)
                        .lineLimit(1)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    count
                }
            }
            .font(.meadow(.ui, 14, current ? .heavy : .semibold))
            .foregroundStyle(current ? meadow.primaryInk : hovering ? meadow.ink : meadow.ink2)
            .padding(.horizontal, open ? 10 : 0)
            .frame(maxWidth: .infinity, minHeight: open ? 36 : 44)
            .background(RoundedRectangle(cornerRadius: 10)
                .fill(current ? meadow.primaryWash : hovering ? meadow.paper3 : .clear))
            .overlay(alignment: .topTrailing) { if !open { badge } }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .focusEffectDisabled()
        .meadowFocusRing(keyed, radius: 10)
        .onHover { hovering = $0 }
        .help(open ? "" : item.title)
        .accessibilityLabel(Text(item.title))
        .accessibilityValue(Text(said))
        .accessibilityAddTraits(current ? .isSelected : [])
    }

    /// The count beside the name (`.ct`): in the quietest ink, or in sunlight with its dot.
    @ViewBuilder private var count: some View {
        switch item.count {
        case .none:
            EmptyView()
        case .plain(let n), .badge(let n):
            Text(verbatim: "\(n)")
                .font(.meadow(.ui, 12, .bold))
                .monospacedDigit()
                .foregroundStyle(meadow.ink3)
        case .sun(let n):
            HStack(spacing: 5) {
                Circle().fill(meadow.sun).frame(width: 7, height: 7)
                Text(verbatim: "\(n)")
            }
            .font(.meadow(.ui, 12, .bold))
            .monospacedDigit()
            .foregroundStyle(meadow.sunInk)
        }
    }

    /// The folded rail's badge, top right: only a count that needs you, or the senders at the gate.
    @ViewBuilder private var badge: some View {
        let (n, sun): (Int?, Bool) = switch item.count {
        case .sun(let n): (n, true)
        case .badge(let n): (n, false)
        case .none, .plain: (nil, false)
        }
        if let n {
            Text(verbatim: "\(n)")
                .font(.meadow(.ui, 10, .bold))
                .monospacedDigit()
                .foregroundStyle(sun ? Color(meadow: 0x2A1A00) : meadow.ink2)
                .padding(.horizontal, 4)
                .frame(minWidth: 16, minHeight: 16)
                .background(Capsule().fill(sun ? meadow.sun : meadow.paper3))
                .background(Capsule().fill(meadow.paper).padding(-2))
                .padding(.top, 2)
                .accessibilityHidden(true)
        }
    }

    private var said: String {
        switch item.count {
        case .none: ""
        case .plain(let n), .badge(let n): "\(n)"
        case .sun(let n): String(localized: "\(n) need you")
        }
    }
}

/// An account's name over its folders (`.folder-acct`), in its colour's dot. Pressing it folds its folders away, or
/// brings them back.
private struct RailAccountName: View {
    let name: String
    let address: String
    let colour: Color
    let folded: Bool
    let toggle: () -> Void
    @Environment(\.meadow) private var meadow
    @State private var hovering = false
    @FocusState private var focused: Bool

    var body: some View {
        Button(action: toggle) {
            HStack(spacing: 8) {
                Circle().fill(colour).frame(width: 8, height: 8)
                Text(name).lineLimit(1)
                Spacer(minLength: 0)
                if hovering || folded || focused {
                    MeadowIconView(folded ? .right : .down, size: 13)
                        .foregroundStyle(meadow.ink3)
                }
            }
            .font(.meadow(.ui, 12.5, .bold))
            .foregroundStyle(meadow.ink2)
            .frame(minHeight: 14)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .focused($focused)
        .focusEffectDisabled()
        .meadowFocusRing(focused)
        .onHover { hovering = $0 }
        .padding(EdgeInsets(top: 10, leading: 10, bottom: 2, trailing: 10))
        .help(address)
        .accessibilityLabel(Text(name))
        .accessibilityValue(Text(folded ? "Folded" : "Shown"))
        .accessibilityHint(Text(folded ? "Shows its folders" : "Hides its folders"))
    }
}

/// A fold in an account's places: its labels, behind one line that opens and closes them.
private struct RailFold: View {
    let title: String
    let icon: MeadowIcon
    let open: Bool
    let toggle: () -> Void
    @Environment(\.meadow) private var meadow
    @State private var hovering = false
    @FocusState private var focused: Bool

    var body: some View {
        Button(action: toggle) {
            HStack(spacing: 11) {
                MeadowIconView(icon)
                Text(title).frame(maxWidth: .infinity, alignment: .leading)
                MeadowIconView(open ? .down : .right, size: 15).foregroundStyle(meadow.ink3)
            }
            .font(.meadow(.ui, 14, .semibold))
            .foregroundStyle(hovering ? meadow.ink : meadow.ink2)
            .padding(.horizontal, 10)
            .frame(maxWidth: .infinity, minHeight: 36)
            .background(RoundedRectangle(cornerRadius: 10).fill(hovering ? meadow.paper3 : .clear))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .focused($focused)
        .focusEffectDisabled()
        .meadowFocusRing(focused, radius: 10)
        .onHover { hovering = $0 }
        .accessibilityValue(Text(open ? "Shown" : "Hidden"))
    }
}
#endif
