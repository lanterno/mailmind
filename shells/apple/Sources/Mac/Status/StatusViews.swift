#if os(macOS)
import SwiftUI

/// The status in the toolbar (the page's `StatusPill`): one thing that matters about syncing, and its menu with the
/// rest.
struct StatusPill: View {
    let status: AppStatus
    @State private var open = false

    var body: some View {
        let pill = status.pill
        Button {
            open.toggle()
        } label: {
            HStack(spacing: 6) {
                Circle().fill(pill.dot.color).frame(width: 7, height: 7)
                Text(pill.text).lineLimit(1)
            }
            .padding(.horizontal, 4)
        }
        .help("Sync, reconcile and curation")
        .accessibilityLabel("Sync status: \(pill.text)")
        .popover(isPresented: $open, arrowEdge: .bottom) {
            StatusMenu(status: status, close: { open = false })
        }
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

/// Across the top of the window, until it is closed, for as long as an account can't connect (`AccountAlert`).
struct AccountAlert: View {
    let status: AppStatus
    @Environment(\.openSettings) private var openSettings

    var body: some View {
        let bad = status.alerting
        if !bad.isEmpty {
            HStack(spacing: 10) {
                Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.yellow)
                Text(words(bad)).lineLimit(2)
                Spacer()
                Button("Open Settings") {
                    status.settingsTab = .accounts
                    openSettings()
                }
                Button {
                    status.alertsClosed.formUnion(bad)
                } label: {
                    Image(systemName: "xmark")
                }
                .buttonStyle(.borderless)
                .accessibilityLabel("Dismiss")
            }
            .font(.callout)
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
            .background(.yellow.opacity(0.14))
            .overlay(alignment: .bottom) { Divider() }
        }
    }

    private func words(_ bad: [String]) -> String {
        let names = bad.map(status.name).joined(separator: ", ")
        return bad.count == 1
            ? String(localized: "1 account couldn’t connect (\(names)).")
            : String(localized: "\(bad.count) accounts couldn’t connect (\(names)).")
    }
}

/// The meeting about to start, beside the status (`SoonPill`), until it is closed.
struct SoonPill: View {
    let status: AppStatus

    var body: some View {
        if let meeting = status.soon {
            let when = meeting.when(now: status.now)
            HStack(spacing: 6) {
                Image(systemName: "calendar")
                Text(meeting.title).fontWeight(.semibold).lineLimit(1)
                Text(when).foregroundStyle(.secondary)
                Button {
                    status.soonClosed.insert(meeting.id)
                } label: {
                    Image(systemName: "xmark.circle.fill").foregroundStyle(.secondary)
                }
                .buttonStyle(.borderless)
                .accessibilityLabel("Dismiss the reminder")
            }
            .help([meeting.detail, meeting.organizer].filter { !$0.isEmpty }.joined(separator: " · "))
            .accessibilityElement(children: .contain)
            .accessibilityLabel(meeting.message(now: status.now) + (meeting.location.isEmpty ? "" : ", \(meeting.location)"))
        }
    }
}
#endif
