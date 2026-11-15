#if os(macOS)
import Observation
import SwiftUI

/// The first-contact gate for one account (`/api/screener`): senders this inbox hasn't decided on, a sender tray
/// rather than a message list. The AI may suggest a verdict for the senders it is sure of, on a press, kept for the
/// session so coming back spends nothing. Only a press on a row decides, and it writes a rule (`/api/rules`).
@MainActor
@Observable
final class ScreenerModel {
    let account: String
    private(set) var queue: API.ScreenerQueue?
    /// The AI's verdicts, by sender, once asked for.
    private(set) var suggestions: [String: API.ScreenerSuggestion]?
    private(set) var suggesting = false
    /// Senders decided on, gone from the tray before the server's answer comes.
    private(set) var decided: Set<String> = []
    @ObservationIgnored let link: CoreLink
    @ObservationIgnored let notices: DeskNotices
    @ObservationIgnored var changed: () -> Void = {}

    init(account: String, link: CoreLink, notices: DeskNotices) {
        self.account = account
        self.link = link
        self.notices = notices
    }

    var rows: [API.ScreenerItem] {
        (queue?.items ?? []).filter { !decided.contains($0.fromAddr) }
    }

    /// How many senders wait for a decision.
    var waiting: Int { queue?.total ?? 0 }

    func load() async {
        guard let routes = link.routes else { return }
        if let read = try? await routes.get(API.Get.Screener.self, ["account": account]) {
            queue = read
            decided.removeAll()
        }
    }

    /// Ask the AI which senders it is sure of.
    func suggest() async {
        guard let routes = link.routes, !suggesting else { return }
        suggesting = true
        defer { suggesting = false }
        do {
            let answer = try await routes.post(API.Post.ScreenerSuggest.self, API.ScreenerSuggestBody(account: account))
            suggestions = Dictionary(answer.suggestions.map { ($0.fromAddr, $0) }, uniquingKeysWith: { first, _ in first })
            if answer.suggestions.isEmpty { notices.say(String(localized: "The AI isn’t sure of any of these")) }
        } catch {
            notices.say(DeskRefusal.isAIOff(error) ? String(localized: "AI is off")
                : DeskRefusal.isOverLimit(error) ? String(localized: "Today’s AI limit is reached")
                : String(localized: "Couldn’t get suggestions"))
        }
    }

    /// Allow a sender, or screen them out.
    func decide(_ sender: String, _ verdict: API.Verdict) async {
        guard let routes = link.routes else { return }
        decided.insert(sender)
        do {
            let changed = try await routes.post(API.Post.Rules.self, API.RuleBody(token: sender, verdict: verdict == .keep ? .keep : .block))
            let short = sender.split(separator: "@").first.map(String.init) ?? sender
            notices.say(verdict == .keep ? String(localized: "Allowed \(short)")
                : String(localized: "Screened out \(short)") + (changed.trashed ? " · trashed" : ""))
            self.changed()
            await load()
        } catch {
            decided.remove(sender)
            notices.say(String(localized: "Couldn’t apply"))
        }
    }

    /// The page's word for each verdict, as a hint names it.
    static func word(_ verdict: API.Verdict) -> String {
        switch verdict {
        case .keep: String(localized: "Allow")
        case .block: String(localized: "Screen out")
        }
    }
}

/// The Screener, in the list's place: each new sender with how much they've sent, the AI's hint where asked for,
/// and Allow or Screen Out.
struct ScreenerScreen: View {
    let model: ScreenerModel

    var body: some View {
        Group {
            if model.queue == nil {
                ProgressView("Loading…").frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if model.rows.isEmpty {
                ContentUnavailableView("Screener clear", systemImage: "checkmark.shield",
                                       description: Text("Every sender in this inbox has been allowed or screened out."))
            } else {
                List {
                    Section {
                        ForEach(model.rows, id: \.fromAddr) { sender in
                            SenderRow(sender: sender, hint: model.suggestions?[sender.fromAddr], model: model)
                        }
                    } header: {
                        bar
                    }
                }
                .listStyle(.inset)
            }
        }
        .task(id: model.account) { await model.load() }
    }

    private var bar: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(model.suggestions.map { "The AI suggests a verdict for \(countNoun($0.count, "sender", "senders")). You still decide." }
                 ?? String(localized: "The AI can suggest a verdict for the senders it’s sure of. You still decide."))
                .font(.callout)
                .textCase(nil)
            Spacer()
            Button(model.suggesting ? "Reading…" : model.suggestions == nil ? "Suggest" : "Suggest Again",
                   systemImage: "sparkles") { Task { await model.suggest() } }
                .disabled(model.suggesting)
                .controlSize(.small)
        }
        .padding(.vertical, 4)
    }
}

private struct SenderRow: View {
    let sender: API.ScreenerItem
    let hint: API.ScreenerSuggestion?
    let model: ScreenerModel

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            SenderInitials(name: sender.fromName, address: sender.fromAddr, size: 30)
            VStack(alignment: .leading, spacing: 2) {
                Text(sender.fromName.isEmpty ? sender.fromAddr : sender.fromName).fontWeight(.semibold)
                Text(verbatim: "\(sender.fromAddr) · \(sender.count) msg · \(sender.when)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if let hint {
                    Label {
                        Text("**\(ScreenerModel.word(hint.verdict))?** \(hint.why)")
                    } icon: {
                        Image(systemName: "sparkles")
                    }
                    .font(.caption)
                    .foregroundStyle(hint.verdict == .keep ? .green : .orange)
                }
            }
            Spacer(minLength: 8)
            VStack(alignment: .trailing, spacing: 4) {
                Button("Allow", systemImage: "checkmark") { Task { await model.decide(sender.fromAddr, .keep) } }
                    .buttonStyle(.bordered)
                    .tint(hint?.verdict == .keep ? .green : nil)
                Button("Screen Out", systemImage: "nosign") { Task { await model.decide(sender.fromAddr, .block) } }
                    .buttonStyle(.bordered)
                    .tint(hint?.verdict == .block ? .orange : nil)
            }
            .controlSize(.small)
        }
        .padding(.vertical, 4)
        // The line between senders runs the row's width, whichever of its texts comes last.
        .alignmentGuide(.listRowSeparatorLeading) { $0[.leading] }
    }
}
#endif
