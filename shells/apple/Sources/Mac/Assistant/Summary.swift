#if os(macOS)
import Observation
import SwiftUI

/// A summary above the mail, as the page's AiCard shows one: the one ingest already wrote, shown at once with no AI
/// call, or the deeper one a person asks for (`/api/ai/summarize`). One card is shown at a time, for the message it is
/// about, and asking again while one is being written asks Claude nothing more.
@MainActor
@Observable
final class SummaryCards {
    enum Card: Equatable {
        case writing
        case failed(String)
        case written(API.Summary)
    }

    struct Shown: Equatable {
        let copy: MailCopy
        let card: Card
    }

    private(set) var shown: Shown?
    @ObservationIgnored private var asking: Set<MailCopy> = []
    @ObservationIgnored let link: CoreLink

    init(link: CoreLink) {
        self.link = link
    }

    /// The card for `copy`, if one was asked for it.
    func card(for copy: MailCopy) -> Card? {
        shown?.copy == copy ? shown?.card : nil
    }

    /// Ask Claude for `copy`'s summary; `force` writes it again even where one is stored.
    func summarize(_ copy: MailCopy, force: Bool = false) async {
        guard let routes = link.routes, !asking.contains(copy) else { return }
        asking.insert(copy)
        defer { asking.remove(copy) }
        show(copy, .writing)
        let body = API.SummarizeBody(folder: copy.folder, force: force ? true : nil, key: copy.key, uid: copy.uid)
        do {
            show(copy, .written(try await routes.post(API.Post.AiSummarize.self, body, asking: link.asksOverLimit)))
        } catch {
            show(copy, .failed(DeskRefusal.isOverLimit(error)
                ? String(localized: "today’s AI limit is reached")
                : DeskRefusal.message(error)))
        }
    }

    private func show(_ copy: MailCopy, _ card: Card) {
        shown = Shown(copy: copy, card: card)
    }
}

/// The card: the stored summary with no call made, Claude reading, what went wrong, or the summary written.
struct SummaryCard: View {
    let copy: MailCopy
    /// The summary and next step ingest stored on the message's row, if the list has it.
    let stored: (summary: String, action: String)?
    let cards: SummaryCards

    var body: some View {
        switch cards.card(for: copy) {
        case nil:
            storedCard
        case .writing:
            DeskCard {
                DeskCardHeading(title: "Summary", symbol: "sparkles")
                DeskWaitingBars(saying: "Reading the thread…")
            }
        case .failed(let why):
            DeskCard {
                HStack(alignment: .firstTextBaseline) {
                    Text(why.isEmpty ? "Couldn’t summarize." : "Couldn’t summarize — \(why).")
                        .foregroundStyle(.red)
                    Button("Retry") { rerun(force: false) }
                        .buttonStyle(.link)
                }
                .font(.callout)
            }
        case .written(let summary):
            written(summary)
        }
    }

    @ViewBuilder private var storedCard: some View {
        let summary = stored?.summary.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let action = stored?.action.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if !summary.isEmpty || !action.isEmpty {
            DeskCard {
                DeskCardHeading(title: "Summary", symbol: "sparkles")
                if !summary.isEmpty { Text(summary).textSelection(.enabled) }
                if !action.isEmpty { SummaryNextStep(action) }
                DeskCardFoot {
                    Text("Saved summary")
                    Text("·")
                    Button("Full Summary") { rerun(force: false) }
                        .buttonStyle(.link)
                        .help("Ask Claude for a deeper summary of the thread")
                }
            }
        }
    }

    private func written(_ summary: API.Summary) -> some View {
        DeskCard {
            DeskCardHeading(title: "Summary", symbol: "sparkles") {
                if summary.cached { CardTag("cached") }
                if summary.stale {
                    HStack(spacing: 4) {
                        Text("thread grew —")
                        Button("re-run") { rerun() }.buttonStyle(.link)
                    }
                    .font(.caption)
                    .foregroundStyle(.orange)
                }
            }
            Text(summary.tldr).textSelection(.enabled)
            if !summary.points.isEmpty {
                VStack(alignment: .leading, spacing: 3) {
                    ForEach(Array(summary.points.enumerated()), id: \.offset) { _, point in
                        HStack(alignment: .firstTextBaseline, spacing: 6) {
                            Text("•").foregroundStyle(.secondary)
                            Text(point).textSelection(.enabled)
                        }
                    }
                }
                .font(.callout)
            }
            if !summary.action.isEmpty { SummaryNextStep(summary.action) }
            DeskCardFoot {
                Text(verbatim: "\(max(summary.messageCount, 1)) msg · \(summary.model)")
                Spacer()
                Button("Re-run", systemImage: "arrow.clockwise") { rerun() }
                    .buttonStyle(.borderless)
                    .labelStyle(.titleAndIcon)
                    .help("Write the summary again (runs the AI)")
            }
        }
    }

    private func rerun(force: Bool = true) {
        Task { await cards.summarize(copy, force: force) }
    }
}

/// What a message asks of you next, as the page's "→ action".
struct SummaryNextStep: View {
    let text: String

    init(_ text: String) {
        self.text = text
    }

    var body: some View {
        Label {
            Text(text).textSelection(.enabled)
        } icon: {
            Image(systemName: "arrow.right")
        }
        .font(.callout.weight(.medium))
        .foregroundStyle(.tint)
    }
}

/// A small word beside a heading: "cached", "invite".
struct CardTag: View {
    let text: LocalizedStringKey

    init(_ text: LocalizedStringKey) {
        self.text = text
    }

    var body: some View {
        Text(text)
            .font(.caption2.weight(.medium))
            .padding(.horizontal, 6)
            .padding(.vertical, 1)
            .background(.quaternary, in: Capsule())
            .foregroundStyle(.secondary)
    }
}
#endif
