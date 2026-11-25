#if os(macOS)
import SwiftUI

// Meadow's top bar (#44), the mockup's `.top`: the sidebar button and the brand on the left, the search in the middle,
// and the status, the light, the keys and Compose on the right, 56 high on the sky. It holds what the window's toolbar
// held: the search field and its suggestions, the status and the meeting about to start, and the outbox.

struct MeadowTopBar: View {
    let app: MailApp
    @Bindable var mailbox: Mailbox
    let shell: MeadowShell
    var status = AppStatus.shared
    /// The room the traffic lights take at the left; none where the bar is drawn without a window's.
    var lights: CGFloat = WindowLights.room
    @Environment(\.meadow) private var meadow
    @Environment(\.meadowLight) private var light
    /// The window is in full screen, where the traffic lights leave the bar.
    @State private var fullScreen = false

    /// The room at the bar's left: the traffic lights', in a window that shows them on the bar; else its own edge.
    static func leading(lights: CGFloat, fullScreen: Bool) -> CGFloat {
        lights == 0 || fullScreen ? 10 : lights
    }

    var body: some View {
        HStack(spacing: 12) {
            HStack(spacing: 4) {
                railButton
                brand
            }
            .fixedSize()
            MeadowSearchBox(app: app, mailbox: mailbox)
                .frame(maxWidth: 720)
                .frame(maxWidth: .infinity)
            HStack(spacing: 6) {
                SoonPill(status: status)
                StatusPill(status: status)
                OutboxButton()
                themeButton
                keysButton
                composeButton
            }
            .fixedSize()
        }
        .padding(.leading, Self.leading(lights: lights, fullScreen: fullScreen))
        .padding(.trailing, 10)
        .frame(height: ShellLayout.topBar)
        .background { WindowDragArea() }
        .background { if lights > 0 { FullScreenWatch { fullScreen = $0 } } }
    }

    private var railButton: some View {
        Button { shell.toggleRail() } label: { MeadowIconView(.sidebar) }
            .meadowButton(.quiet, icon: true)
            .help("Show or hide mailboxes ([)")
            .accessibilityLabel(shell.docked ? (shell.railOpen ? "Show mailbox icons only" : "Show mailbox names")
                : "Mailboxes")
    }

    private var brand: some View {
        MeadowBrand { app.place = .desk(.brief) }
    }

    private var themeButton: some View {
        Button { status.nextLight() } label: { MeadowIconView(light.icon) }
            .meadowButton(icon: true)
            .help("Automatic, Day, Dusk or Night")
            .accessibilityLabel("Light: \(status.theme.name) — switch to \(status.theme.next.name)")
    }

    private var keysButton: some View {
        Button { status.showingKeys = true } label: { MeadowIconView(.keyboard) }
            .meadowButton(icon: true)
            .help("Keyboard shortcuts (?)")
            .accessibilityLabel("Keyboard shortcuts")
    }

    private var composeButton: some View {
        Button {
            Composing.shared.newMessage(current: app.place?.account)
        } label: {
            MeadowIconView(.pen)
            Text("Compose")
            MeadowKey("C")
        }
        .meadowButton(.primary)
        .help("Compose (c)")
    }
}

/// The brand (`.brand`): the mark and MailMind in the display face, which goes to today's brief.
struct MeadowBrand: View {
    let action: () -> Void
    @Environment(\.meadow) private var meadow
    @FocusState private var focused: Bool

    var body: some View {
        Button(action: action) {
            HStack(spacing: 9) {
                MeadowBrandMark(size: 28)
                Text(verbatim: "MailMind")
                    .font(.meadow(.display, 18, .bold))
                    .foregroundStyle(meadow.ink)
                    .lineLimit(1)
            }
            .padding(.vertical, 4)
            .padding(.horizontal, 6)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .focused($focused)
        .focusEffectDisabled()
        .meadowFocusRing(focused, radius: 10)
        .accessibilityLabel("MailMind — today’s brief")
    }
}

/// The search (`.search`): a box of raised paper, up to 720 wide, that searches as the window's search field did:
/// `?words` asks your inbox, `!words` plans a bulk action, anything else searches, with the server's suggestions under
/// it as you type. Its ⌘K opens the palette; `/` and Search (⌘F) put the cursor in it.
struct MeadowSearchBox: View {
    let app: MailApp
    @Bindable var mailbox: Mailbox
    @State private var text = ""
    @State private var offers: [API.SearchSuggestion] = []
    @State private var at: Int?
    @State private var hovering = false
    @FocusState private var focused: Bool
    @Environment(\.meadow) private var meadow

    static let prompt = String(localized: "Search mail, jump anywhere, or ask…")
    /// With AI turned off nothing is asked.
    static let promptWithoutAI = String(localized: "Search mail, jump anywhere…")

    private var prompt: String { mailbox.aiTurnedOff ? Self.promptWithoutAI : Self.prompt }

    var body: some View {
        HStack(spacing: 10) {
            MeadowIconView(.search)
                .foregroundStyle(meadow.ink3)
            field
            Button { mailbox.paletteOpen = true } label: { MeadowKey("⌘K") }
                .buttonStyle(.plain)
                .focusEffectDisabled()
                .help("Command palette (⌘K)")
                .accessibilityLabel("Command palette")
        }
        .padding(.leading, 14)
        .padding(.trailing, 10)
        .frame(height: 40)
        .frame(maxWidth: .infinity)
        .background {
            RoundedRectangle(cornerRadius: 12)
                .fill(hovering || focused ? meadow.paperRaised : meadow.paperRaised.opacity(meadow.seen(0.9)))
                .overlay {
                    RoundedRectangle(cornerRadius: 12).strokeBorder(meadow.line2.opacity(meadow.seen(0.7)), lineWidth: 1)
                }
        }
        .contentShape(RoundedRectangle(cornerRadius: 12))
        .onTapGesture { focused = true }
        .onHover { hovering = $0 }
        .meadowFocusRing(focused, radius: 12)
        .overlay(alignment: .topLeading) {
            if focused, !offers.isEmpty {
                suggestions
                    .offset(y: 46)
            }
        }
        .task(id: text) { await lookUp(text) }
        .onChange(of: text) { _, now in
            at = nil
            // Clearing the field leaves the search, as Esc does in the page.
            if now.isEmpty, app.place?.search != nil { mailbox.leaveSearch() }
        }
        .onChange(of: app.place, initial: true) { _, place in
            let search = place?.search
            text = search.map { $0.asked ?? $0.q } ?? ""
        }
        // Asked for while the mail window was closed (Mailbox › Search), it is taken as the window opens again, once
        // the window can hold the keyboard.
        .onChange(of: mailbox.searchFocus, initial: true) { _, wanted in
            guard wanted else { return }
            focused = true
            mailbox.searchFocus = false
            Task { focused = true }
        }
        // Return leaves the keyboard here, as the page's does: the empty pane says ↓, which goes on to the list.
        .onChange(of: focused, initial: true) { _, now in mailbox.inSearchField = now }
    }

    private var field: some View {
        // The prompt is drawn here in the light's ink: a field's own placeholder takes AppKit's grey.
        ZStack(alignment: .leading) {
            if text.isEmpty {
                Text(prompt)
                    .foregroundStyle(meadow.ink3)
                    .lineLimit(1)
                    .allowsHitTesting(false)
                    .accessibilityHidden(true)
            }
            TextField("", text: $text)
                .accessibilityLabel(prompt)
                .textFieldStyle(.plain)
                .foregroundStyle(meadow.ink)
                .focused($focused)
                .onSubmit(submit)
                .onExitCommand(perform: leave)
                .onKeyPress(.downArrow) { step(1) }
                .onKeyPress(.upArrow) { step(-1) }
                .onKeyPress(.tab) {
                    guard let at, offers.indices.contains(at) else { return .ignored }
                    take(offers[at], run: false)
                    return .handled
                }
        }
        .font(.meadow(.ui, 14.5))
        .frame(maxWidth: .infinity)
    }

    /// What the server finishes the word being typed with, as a popover's list (`.pop`, `.pal-i`).
    private var suggestions: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(Array(offers.enumerated()), id: \.element.value) { index, offer in
                Button { take(offer, run: true) } label: {
                    HStack(spacing: 12) {
                        Text(offer.label).foregroundStyle(meadow.ink).lineLimit(1)
                        if !offer.detail.isEmpty {
                            Text(offer.detail).font(.meadow(.ui, 12.5)).foregroundStyle(meadow.ink3).lineLimit(1)
                        }
                        Spacer(minLength: 0)
                    }
                    .font(.meadow(.ui, 14))
                    .padding(.horizontal, 10)
                    .padding(.vertical, 4)
                    .frame(minHeight: 38)
                    .background(RoundedRectangle(cornerRadius: 10).fill(index == at ? meadow.primaryWash : .clear))
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .onHover { if $0 { at = index } }
            }
        }
        .padding(6)
        .meadowPanel(radius: 14, raised: true)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Suggestions")
    }

    /// ↑ and ↓ through the suggestions; with none, ↓ goes on to the list, whose first row opens.
    private func step(_ by: Int) -> KeyPress.Result {
        guard !offers.isEmpty else {
            guard by > 0, (app.list?.total ?? 0) > 0 else { return .ignored }
            focused = false
            mailbox.enterList(in: NSApp.keyWindow)
            return .handled
        }
        at = at.map { ($0 + by + offers.count) % offers.count } ?? (by > 0 ? 0 : offers.count - 1)
        return .handled
    }

    private func submit() {
        if let at, offers.indices.contains(at) {
            take(offers[at], run: true)
            return
        }
        offers = []
        mailbox.submit(text)
    }

    /// A suggestion taken: its words in the box; an operator goes on to its values, a whole one searches.
    private func take(_ offer: API.SearchSuggestion, run: Bool) {
        text = offer.value
        at = nil
        offers = []
        if offer.complete, run { mailbox.submit(offer.value) }
    }

    /// Esc: out of the search and the box, back to where the search came from.
    private func leave() {
        offers = []
        text = ""
        focused = false
        mailbox.leaveSearch()
    }

    /// What the server finishes the word being typed with, a moment after the typing stops (`WAIT`).
    private func lookUp(_ typed: String) async {
        offers = []
        guard focused, !typed.trimmingCharacters(in: .whitespaces).isEmpty,
              !typed.hasPrefix("?"), !typed.hasPrefix("!"), typed != app.place?.search?.q else { return }
        try? await AppSeams.clock.sleep(for: .milliseconds(140))
        guard !Task.isCancelled else { return }
        let found = await mailbox.suggestions(for: typed)
        guard !Task.isCancelled else { return }
        var seen = Set<String>()
        offers = found.filter { seen.insert($0.value).inserted }
    }
}
#endif
