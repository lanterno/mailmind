#if os(macOS)
import AppKit
import Observation
import SwiftUI

/// Mail that landed somewhere other than the list on screen, as the page's NewMail pill says it: how many came, how
/// many came back from a snooze, and with the AI on, a line on what came in since the Inbox was last looked at. The
/// list on screen reads its own mail again, so what lands there isn't said here. When each Inbox was last looked at is
/// kept for this run only, so nothing about it is written anywhere.
@MainActor
@Observable
final class SinceLooked {
    struct Pending: Equatable {
        var count = 0
        var woke = 0
        /// The one account it all came to, or "" for more than one.
        var account = ""
        /// When that Inbox was last looked at, which the line is written from.
        var since: Date
        var headline = ""
    }

    /// What a `new-mail` event says.
    struct Arrived: Decodable, Sendable {
        var account: String?
        var folder: String?
        var count: Int?
    }

    /// What a `snooze-wake` event says.
    struct Woke: Decodable, Sendable {
        var accounts: [String]?
        var count: Int?
    }

    /// An Inbox never looked at in this run was looked at this long ago, as the page's first look.
    static let firstLook: TimeInterval = 6 * 3600
    /// A burst of mail is one line: it's written this long after the last of it.
    static let wait = Duration.milliseconds(1500)

    private(set) var pending: Pending?
    @ObservationIgnored var isActive: @MainActor () -> Bool = { NSApp?.isActive ?? true }
    @ObservationIgnored var now: @MainActor () -> Date = Date.init
    @ObservationIgnored private let link: CoreLink
    @ObservationIgnored private var looks: [String: Date] = [:]
    @ObservationIgnored private var aiOn = false
    @ObservationIgnored private var writing = false
    @ObservationIgnored private var soon: Task<Void, Never>?

    init(link: CoreLink) {
        self.link = link
    }

    /// The pill's words: "▾ 3 new, 1 back from snooze — open the Inbox".
    var words: String? {
        guard let pending else { return nil }
        let parts = [pending.count > 0 ? "\(pending.count) new" : nil,
                     pending.woke > 0 ? "\(pending.woke) back from snooze" : nil].compactMap { $0 }
        return "▾ \(parts.joined(separator: ", ")) — open the Inbox"
    }

    /// The Inbox the pill opens: the account's, or every account's.
    var inbox: Place {
        guard let account = pending?.account, !account.isEmpty else { return .allInboxes }
        return .folder(account: account, folder: "INBOX")
    }

    /// Whether mail to `account`'s `folder` lands in the list on screen (`landedHere`).
    static func landsOn(_ place: Place?, account: String, folder: String) -> Bool {
        switch place {
        case .allInboxes?: folder.uppercased() == "INBOX"
        case .folder(let shown, let name)?: shown == account && name.uppercased() == folder.uppercased()
        default: false
        }
    }

    /// An Inbox on screen was looked at: from now its new mail counts from here, and the pill goes (`lookedAt`). Only
    /// a real look moves it: an Inbox opened, or the app come back to with one on screen.
    func lookedAt(_ place: Place?, accounts: [String]) {
        let inboxes: [String] = switch place {
        case .allInboxes?: accounts
        case .folder(let account, let folder)? where folder.uppercased() == "INBOX": [account]
        default: []
        }
        guard !inboxes.isEmpty else { return }
        for account in inboxes { looks[account] = now() }
        pending = nil
        soon?.cancel()
    }

    /// New mail (`onNewMail`): said unless it landed on screen, and its line written a moment after the burst.
    func newMail(_ said: Arrived, on place: Place?, ai: Bool) {
        let account = said.account ?? "", folder = said.folder ?? "INBOX"
        guard !Self.landsOn(place, account: account, folder: folder) else { return }
        add(count: max(said.count ?? 1, 1), woke: 0, account: account)
        aiOn = ai
        writeSoon()
    }

    /// Mail back from a snooze with no Inbox on screen to show it (`onSnoozeWake`).
    func snoozeWake(_ said: Woke, on place: Place?) {
        let accounts = said.accounts ?? []
        let account = accounts.count == 1 ? accounts[0] : ""
        guard !Self.landsOn(place, account: account, folder: "INBOX") else { return }
        add(count: 0, woke: max(said.count ?? 1, 1), account: account)
    }

    /// The person is back at the app: a line not yet written is written now (`cameBack`).
    func cameBack() {
        guard let pending, pending.headline.isEmpty else { return }
        writeSoon()
    }

    private func add(count: Int, woke: Int, account: String) {
        let was = pending
        pending = Pending(count: (was?.count ?? 0) + count, woke: (was?.woke ?? 0) + woke,
                          account: was.map { $0.account == account ? account : "" } ?? account,
                          since: was?.since ?? looks[account] ?? now().addingTimeInterval(-Self.firstLook),
                          headline: was?.headline ?? "")
    }

    private func writeSoon() {
        soon?.cancel()
        soon = Task { [weak self] in
            try? await Task.sleep(for: Self.wait)
            guard !Task.isCancelled else { return }
            await self?.writeHeadline()
        }
    }

    /// The line on what came in, read if one was written, else written (`/api/briefing/since`). Only with someone at
    /// the app, with the AI on, and never over the day's limit: then the plain count stands.
    func writeHeadline() async {
        guard let asked = pending, asked.count > 0, aiOn, !writing, isActive(), let routes = link.routes else { return }
        writing = true
        defer { writing = false }
        let since = Int(asked.since.timeIntervalSince1970)
        var line = ""
        do {
            switch try await routes.get(API.Get.BriefingSince.self, ["since": String(since), "account": asked.account]) {
            case .delta(let delta): line = delta.empty ? "" : delta.headline
            case .deltaEmpty: break
            case .deltaMissing:
                if case .delta(let delta) = try await routes.post(API.Post.BriefingSince.self,
                                                                  API.DeltaBody(account: asked.account, since: .int(since))) {
                    line = delta.empty ? "" : delta.headline
                }
            }
        } catch {
            return
        }
        guard !line.isEmpty, pending != nil else { return }
        pending?.headline = line
    }
}

/// The pill over the list: a press opens the Inbox the mail came to.
struct DeskNewMailPill: View {
    @Environment(Desk.self) private var desk: Desk?

    var body: some View {
        if let desk, let words = desk.since.words {
            Button {
                desk.go(desk.since.inbox)
            } label: {
                VStack(alignment: .leading, spacing: 2) {
                    Text(words).font(.callout.weight(.semibold))
                    if let line = desk.since.pending?.headline, !line.isEmpty {
                        Text(line).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                    }
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 7)
                .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14))
                .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(.tint.opacity(0.5)))
                .shadow(radius: 4, y: 1)
            }
            .buttonStyle(.plain)
            .padding(.top, 8)
            .padding(.horizontal, 12)
            .transition(.move(edge: .top).combined(with: .opacity))
            .accessibilityHint("Opens the Inbox")
        }
    }
}
#endif
