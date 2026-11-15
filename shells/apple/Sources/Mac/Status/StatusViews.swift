#if os(macOS)
import SwiftUI

/// The status in the top bar (the mockup's `.status`, the page's `StatusPill`): one thing that matters about syncing,
/// on a pill of paper over the sky, and its menu with the rest.
struct StatusPill: View {
    let status: AppStatus
    @State private var open = false
    @State private var hovering = false
    @FocusState private var focused: Bool
    @Environment(\.meadow) private var meadow

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
        .help("Sync, reconcile and curation")
        .accessibilityLabel("Sync status: \(pill.text)")
        .popover(isPresented: $open, arrowEdge: .bottom) {
            StatusMenu(status: status, close: { open = false })
        }
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

/// What the status says when asked (`StatusMenu`): how syncing stands, the Inboxes and AI, each account when there
/// is more than one or one fails, and what a person can start from here.
struct StatusMenu: View {
    let status: AppStatus
    let close: () -> Void
    @Environment(\.openSettings) private var openSettings
    @State private var inboxes: Int?
    @State private var notAsked = false

    var body: some View {
        let pill = status.pill
        let digest = status.digest
        let bad = StatusWords.bad(status.addresses, digest: digest, health: status.health)
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Circle().fill(pill.dot.color).frame(width: 9, height: 9)
                Text(pill.text).font(.headline)
            }
            if digest?.profile == "read-only" {
                small(String(localized: "MailMind is running read-only: it writes nothing to your mail servers and syncs only when asked. What you file, star or send waits for a full run."))
            }
            if let sync = digest?.sync, sync.last != nil, !sync.result.isEmpty {
                small(String(localized: "Last sync: \(sync.result)"))
            }
            small(inboxLine(summarized: digest?.stats.summarized ?? 0))
            if let ai = digest?.ai {
                let line = StatusWords.ai(ai)
                Label { Text(line.text) } icon: { Image(systemName: "wind") }
                    .font(.callout)
                    .foregroundStyle(line.failing ? .red : .secondary)
                if ai.engine != "none" {
                    Label { Text(StatusWords.aiPrivacy) } icon: { Image(systemName: "lock") }
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            let accounts = status.addresses
            if accounts.count > 1 || !bad.isEmpty {
                Divider()
                ForEach(accounts, id: \.self) { account in
                    let standing = StatusWords.standing(of: account, digest: digest, health: status.health)
                    HStack(spacing: 8) {
                        Circle().fill(standing.color).frame(width: 7, height: 7)
                            .opacity(standing == .unknown ? 0.3 : 1)
                        Text(status.name(account))
                        Spacer()
                        Text(standing.words).font(.caption)
                            .foregroundStyle(standing == .cantConnect ? .red : .secondary)
                    }
                    .accessibilityElement(children: .combine)
                }
            }
            Divider()
            VStack(alignment: .leading, spacing: 2) {
                action("Sync Now", "arrow.clockwise", disabled: status.syncing) { Task { await status.syncNow() } }
                action("Reconcile", "arrow.uturn.backward", note: String(localized: "drop mail deleted elsewhere"),
                       disabled: status.reconciling) { Task { await status.reconcile() } }
                if let curate = status.curate {
                    action("Curate", "leaf", note: String(localized: "noisy senders, rules, audit"), run: curate)
                }
                if !bad.isEmpty {
                    action("Fix Connections in Settings", "slider.horizontal.3") {
                        status.settingsTab = .accounts
                        openSettings()
                    }
                }
                if notAsked {
                    action("Turn On Notifications", "bell") {
                        Task {
                            let yes = await status.newMail.ask()
                            status.say(yes ? String(localized: "Notifications on") : String(localized: "Notifications blocked"))
                        }
                    }
                }
            }
        }
        .padding(16)
        .frame(width: 340, alignment: .leading)
        .task {
            notAsked = await status.newMail.allowed() == nil
            // The page counts the Inboxes on their servers only when its menu opens: an IMAP scan.
            inboxes = try? await status.core?.get(API.Get.Stats.self).totals.inInbox
        }
    }

    private func inboxLine(summarized: Int) -> String {
        let first = inboxes.map { String(localized: "\($0.formatted()) in your inboxes") }
            ?? String(localized: "counting your inboxes…")
        return first + " · " + String(localized: "\(summarized.formatted()) summarized")
    }

    private func small(_ text: String) -> some View {
        Text(text).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
    }

    private func action(_ title: LocalizedStringKey, _ symbol: String, note: String? = nil, disabled: Bool = false,
                        run: @escaping @MainActor () -> Void) -> some View
    {
        Button {
            close()
            run()
        } label: {
            HStack(spacing: 8) {
                Image(systemName: symbol).frame(width: 18)
                Text(title)
                if let note { Text(note).font(.caption).foregroundStyle(.secondary) }
                Spacer()
            }
            .contentShape(Rectangle())
            .padding(.vertical, 4)
        }
        .buttonStyle(.plain)
        .disabled(disabled)
    }
}

/// Under the top bar, until it is closed, for as long as an account can't connect (`AccountAlert`), in the light's
/// sun.
struct AccountAlert: View {
    let status: AppStatus
    @Environment(\.openSettings) private var openSettings
    @Environment(\.meadow) private var meadow

    var body: some View {
        let bad = status.alerting
        if !bad.isEmpty {
            HStack(spacing: 10) {
                MeadowIconView(.alert).foregroundStyle(meadow.sunInk)
                Text(words(bad)).lineLimit(2).foregroundStyle(meadow.ink2)
                Spacer()
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

    private func words(_ bad: [String]) -> String {
        let names = bad.map(status.name).joined(separator: ", ")
        return bad.count == 1
            ? String(localized: "1 account couldn’t connect (\(names)).")
            : String(localized: "\(bad.count) accounts couldn’t connect (\(names)).")
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
