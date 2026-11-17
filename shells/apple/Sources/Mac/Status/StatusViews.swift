#if os(macOS)
import SwiftUI

/// The status in the top bar (the mockup's `.status`, the page's `StatusPill`): one thing that matters about syncing,
/// on a pill of paper over the sky, and its menu with the rest.
struct StatusPill: View {
    let status: AppStatus
    @State private var open = false
    @State private var hovering = false
    @State private var menu = MeadowMenuModel()
    @FocusState private var focused: Bool
    @Environment(\.meadow) private var meadow
    @Environment(\.openSettings) private var openSettings

    var body: some View {
        let pill = status.pill
        Button {
            open.toggle()
        } label: {
            HStack(spacing: 8) {
                MeadowStatusDot(dot: pill.dot)
                Text(pill.text).lineLimit(1)
            }
            .font(.meadow(.ui, 12.5, .semibold))
            .foregroundStyle(meadow.ink2)
            .padding(.horizontal, 12)
            .frame(height: 34)
            .background(Capsule().fill(hovering ? meadow.paper3 : meadow.paper.opacity(0.62)))
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .focused($focused)
        .focusEffectDisabled()
        .meadowFocusRing(focused, radius: 17)
        .onHover { hovering = $0 }
        .help("Sync, checks against the server, and tidying up")
        .accessibilityLabel("Sync status: \(pill.text)")
        .meadowPopover(isPresented: $open, keys: { menu.key($0) }) {
            // Settings opens from the window's own environment: the popover's panel is outside the app's scenes.
            StatusMenu(status: status, model: menu, settings: { openSettings() }, close: { open = false })
        }
        .onChange(of: open) { _, shown in if shown { menu.at = 0 } }
    }
}

/// The status's dot (`.dot`): moss once synced, sun while it works, rust when it fails, each with a ring of itself.
struct MeadowStatusDot: View {
    let dot: StatusWords.Dot
    @Environment(\.meadow) private var meadow

    var body: some View {
        let (color, ring): (Color, Double) = switch dot {
        case .ok: (meadow.moss, 0.22)
        case .busy: (meadow.sun, 0.25)
        case .error: (meadow.rust, 0.25)
        case .none: (meadow.ink3, 0.22)
        }
        Circle()
            .fill(color)
            .frame(width: 8, height: 8)
            .background { Circle().fill(color.opacity(ring)).frame(width: 14, height: 14) }
            .accessibilityHidden(true)
    }
}

/// What the status says when asked (`StatusMenu`, the mockup's `syncPop()`): each account and how it stands, and what
/// a person can start from here, on a popover's paper; above those, a read-only run's note and AI that is failing.
struct StatusMenu: View {
    let status: AppStatus
    let model: MeadowMenuModel
    let settings: @MainActor () -> Void
    let close: @MainActor () -> Void
    @Environment(\.meadow) private var meadow
    @State private var notAsked = false

    var body: some View {
        MeadowMenu(parts: parts, width: 280, model: model, close: close, label: String(localized: "Sync status"))
            .task { notAsked = await status.newMail.allowed() == nil }
            .task { await status.readJobs() }
    }

    /// How an account stands on the right of its row: can't connect, syncing, or when its mail last came in.
    static func state(of account: String, digest: API.Digest?, health: [API.AccountStanding],
                      now: Date) -> (standing: StatusWords.Standing, words: String) {
        let standing = StatusWords.standing(of: account, digest: digest, health: health)
        switch standing {
        case .cantConnect: return (standing, String(localized: "Can’t connect"))
        case .syncing: return (standing, String(localized: "Syncing…"))
        case .connected, .unknown:
            if case .synced(let when, _) = StatusWords.healthLine(health.first { $0.account == account }, now: now),
               !when.isEmpty {
                return (standing, String(localized: "Synced \(when)"))
            }
            return (standing, standing == .connected ? String(localized: "Connected") : "")
        }
    }

    private var parts: [MeadowMenu.Part] {
        let digest = status.digest
        let bad = StatusWords.bad(status.addresses, digest: digest, health: status.health)
        var parts: [MeadowMenu.Part] = []
        if !status.addresses.isEmpty {
            parts.append(.title(String(localized: "Accounts")))
            parts.append(.view(AnyView(accounts)))
            parts.append(.rule)
        }
        if let note = note(digest) {
            parts.append(.view(AnyView(note)))
            parts.append(.rule)
        }
        parts.append(.item(MeadowMenuItem(id: "sync", title: String(localized: "Check for mail now"), icon: .refresh,
                                          disabled: status.syncing) { [status] in Task { await status.syncNow() } }))
        parts.append(.item(MeadowMenuItem(id: "reconcile", title: String(localized: "Check Mail Against the Server"),
                                          detail: String(localized: "Drop mail deleted elsewhere"), icon: .restore,
                                          disabled: status.reconciling) {
            [status] in Task { await status.reconcile() }
        }))
        if let curate = status.curate {
            parts.append(.item(MeadowMenuItem(id: "curate",
                                              title: String(localized: "Tidy up — senders flooding you"),
                                              icon: .archive, run: curate)))
        }
        if notAsked {
            parts.append(.item(MeadowMenuItem(id: "notify", title: String(localized: "Turn on notifications"),
                                              icon: .bell) { [status] in
                Task {
                    let yes = await status.newMail.ask()
                    status.say(yes ? String(localized: "Notifications on") : String(localized: "Notifications blocked"))
                }
            }))
        }
        parts.append(.item(MeadowMenuItem(id: "settings", title: bad.isEmpty
            ? String(localized: "Sync & account settings") : String(localized: "Fix connections in Settings"),
                                          icon: bad.isEmpty ? .sliders : .alert) { [status, settings] in
            status.settingsTab = .accounts
            settings()
        }))
        return parts
    }

    /// Each account (`.health`): its dot, its name and address, and how it stands.
    private var accounts: some View {
        VStack(spacing: 0) {
            ForEach(status.addresses, id: \.self) { account in
                let state = Self.state(of: account, digest: status.digest, health: status.health, now: status.now)
                MeadowHealthRow(dot: dot(state.standing), title: status.name(account), detail: account,
                                state: state.words, stateColor: state.standing == .cantConnect ? meadow.rust : nil)
            }
        }
    }

    private func dot(_ standing: StatusWords.Standing) -> Color {
        switch standing {
        case .connected: meadow.moss
        case .syncing: meadow.sun
        case .cantConnect: meadow.rust
        case .unknown: meadow.ink3.opacity(0.5)
        }
    }

    /// What a person should know before they start anything: that the run is read-only, AI failing, and background
    /// work that isn't going through, in rust. The day's spend and how AI treats mail are Settings' to say.
    private func note(_ digest: API.Digest?) -> AnyView? {
        let readOnly = digest?.profile == "read-only"
        let ai = digest.map { StatusWords.ai($0.ai) }
        let failing = StatusWords.failingLine(status.failingJobs)
        guard readOnly || ai?.failing == true || failing != nil else { return nil }
        return AnyView(VStack(alignment: .leading, spacing: 4) {
            if readOnly {
                line(String(localized: """
                    MailMind is running read-only: it writes nothing to your mail servers and syncs only when asked. \
                    What you file, star or send waits for a full run.
                    """))
            }
            if let ai, ai.failing {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    MeadowIconView(.wind, size: 13).alignmentGuide(.firstTextBaseline) { $0[.bottom] - 2 }
                    line(ai.text, colour: meadow.rust)
                }
                .foregroundStyle(meadow.rust)
            }
            if let failing {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    MeadowIconView(.alert, size: 13).alignmentGuide(.firstTextBaseline) { $0[.bottom] - 2 }
                    line(failing, colour: meadow.rust)
                }
                .foregroundStyle(meadow.rust)
            }
        }
        .padding(.vertical, 6)
        .padding(.horizontal, 10)
        .frame(maxWidth: .infinity, alignment: .leading))
    }

    private func line(_ text: String, colour: Color? = nil) -> some View {
        Text(text)
            .font(.meadow(.ui, 12.5))
            .foregroundStyle(colour ?? meadow.ink3)
            .fixedSize(horizontal: false, vertical: true)
    }
}


/// Under the top bar, until it is closed, for as long as an account can't connect (`AccountAlert`), in the light's
/// sun: Proton Bridge not running, or Gmail wanting an App Password, said as such with the page that helps.
struct AccountAlert: View {
    let status: AppStatus
    @Environment(\.openSettings) private var openSettings
    @Environment(\.meadow) private var meadow

    var body: some View {
        let bad = status.alerting
        if !bad.isEmpty {
            let said = StatusWords.alert(bad, health: status.health, type: status.type, name: status.name)
            HStack(spacing: 10) {
                MeadowIconView(.alert).foregroundStyle(meadow.sunInk)
                Text(said.text).lineLimit(2).foregroundStyle(meadow.ink2)
                Spacer()
                if let link = said.link { LinkButton(link: link) }
                Button("Open Settings") {
                    status.settingsTab = .accounts
                    openSettings()
                }
                .meadowButton(small: true)
                Button {
                    status.alertsClosed.formUnion(bad)
                } label: {
                    MeadowIconView(.x, size: 15)
                }
                .meadowButton(.quiet, small: true, icon: true)
                .accessibilityLabel("Dismiss")
            }
            .font(.meadow(.ui, 13))
            .padding(.leading, 12)
            .padding(.trailing, 8)
            .padding(.vertical, 6)
            .background(RoundedRectangle(cornerRadius: 12).fill(meadow.sunWash))
        }
    }
}

/// The meeting about to start, beside the status (`SoonPill`), until it is closed: a pill in the sun's wash, ringed in
/// sun, as the page's top bar shows it.
struct SoonPill: View {
    let status: AppStatus
    @Environment(\.meadow) private var meadow

    var body: some View {
        if let meeting = status.soon {
            let when = meeting.when(now: status.now)
            HStack(spacing: 0) {
                HStack(spacing: 7) {
                    MeadowIconView(.cal, size: 15)
                    Text(meeting.title).fontWeight(.semibold).foregroundStyle(meadow.ink).lineLimit(1)
                        .frame(maxWidth: 200, alignment: .leading)
                    Text(when).lineLimit(1)
                }
                .padding(.leading, 12)
                .padding(.trailing, 4)
                Button {
                    status.soonClosed.insert(meeting.id)
                } label: {
                    MeadowIconView(.x, size: 13)
                        .padding(.leading, 6)
                        .padding(.trailing, 11)
                        .frame(maxHeight: .infinity)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .opacity(0.7)
                .accessibilityLabel("Dismiss the reminder")
            }
            .font(.meadow(.ui, 12.5, .semibold))
            .foregroundStyle(meadow.sunInk)
            .frame(height: 32)
            .background(Capsule().fill(meadow.sunWash))
            .overlay(Capsule().strokeBorder(meadow.sun.opacity(0.45), lineWidth: 1))
            .help([meeting.detail, meeting.organizer].filter { !$0.isEmpty }.joined(separator: " · "))
            .accessibilityElement(children: .contain)
            .accessibilityLabel(meeting.message(now: status.now)
                                + (meeting.location.isEmpty ? "" : ", \(meeting.location)"))
        }
    }
}
#endif
