#if os(macOS)
import Observation
import SwiftUI

/// A summary above the mail, as the page's AiCard shows one: the one ingest already wrote, shown at once with no AI
/// call, or the deeper one a person asks for (`/api/ai/summarize`). One card is shown at a time, for the message it is
/// about, and asking again while one is being written asks the AI nothing more.
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

    /// Ask the AI for `copy`'s summary; `force` writes it again even where one is stored.
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

/// The card, as Meadow's summary note draws it (`.note`, #44): the stored summary with no call made, the AI reading,
/// what went wrong, or the summary written; its lead in moss, its points, the next step with the reply written ahead
/// when there is one, and a foot that says whose words these are not.
struct SummaryCard: View {
    let copy: MailCopy
    /// The summary and next step ingest stored on the message's row, if the list has it.
    let stored: (summary: String, action: String)?
    let cards: SummaryCards
    /// Who wrote the message, for the foot: "Not Ren's words".
    var author: String?

    var body: some View {
        switch cards.card(for: copy) {
        case nil:
            storedCard
        case .writing:
            SummaryNote(source: String(localized: "Reading the thread…")) {
                DeskWaitingBars(saying: "Reading the thread…")
                    .padding(.top, 2)
            }
        case .failed(let why):
            SummaryNote(source: "") {
                SummaryFailed(why: why) { rerun(force: false) }
            }
        case .written(let summary):
            written(summary)
        }
    }

    @ViewBuilder private var storedCard: some View {
        let summary = stored?.summary.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let action = stored?.action.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if !summary.isEmpty || !action.isEmpty {
            SummaryNote(source: Self.source(1)) {
                if !summary.isEmpty { SummaryLead(text: summary) }
                if !action.isEmpty { SummaryNext(text: action, copy: copy) }
                SummaryFoot(author: author, help: String(localized: "Ask the AI for a deeper summary of the thread")) {
                    rerun(force: false)
                }
            }
        }
    }

    private func written(_ summary: API.Summary) -> some View {
        SummaryNote(source: Self.source(max(summary.messageCount, 1)), grew: summary.stale ? { rerun() } : nil) {
            SummaryLead(text: summary.tldr)
            if !summary.points.isEmpty { SummaryPoints(points: summary.points) }
            if !summary.action.isEmpty { SummaryNext(text: summary.action, copy: copy) }
            SummaryFoot(author: author, help: String(localized: "Write the summary again (runs the AI)")) { rerun() }
        }
    }

    /// "MailMind's reading of 3 messages".
    static func source(_ count: Int) -> String {
        count == 1 ? String(localized: "MailMind’s reading of 1 message")
            : String(localized: "MailMind’s reading of \(count) messages")
    }

    private func rerun(force: Bool = true) {
        Task { await cards.summarize(copy, force: force) }
    }
}

/// The note (`.note`): the moss wash under the paper's grain, radius 14, the AI's mark and what it read.
private struct SummaryNote<Content: View>: View {
    let source: String
    /// Re-run, when the thread grew since the summary was written: said in sunlight in its head.
    var grew: (() -> Void)?
    @ViewBuilder let content: Content
    @Environment(\.meadow) private var meadow

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // The head wraps as the page's does: in the aside's narrow column what was read goes under the mark.
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 8) {
                    mark
                    Spacer(minLength: 8)
                    read.fixedSize()
                }
                VStack(alignment: .leading, spacing: 6) {
                    mark
                    read
                }
            }
            .padding(.bottom, 8)
            content
        }
        .padding(.top, 14)
        .padding(.horizontal, 16)
        .padding(.bottom, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background {
            ZStack {
                meadow.mossWash
                MeadowGrain()
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: 14))
        .accessibilityElement(children: .contain)
        .accessibilityLabel("MailMind summary")
    }

    private var mark: some View {
        HStack(spacing: 8) {
            MeadowAIMark("Summary")
            if let grew {
                Button(action: grew) { SummaryMark(text: String(localized: "Thread grew")) }
                    .buttonStyle(.plain)
                    .help("The thread grew since this was written — write it again (runs the AI)")
            }
        }
    }

    private var read: some View {
        Text(source)
            .font(.meadow(.ui, 12))
            .foregroundStyle(meadow.ink3)
            .lineLimit(1)
    }
}

/// A word in sunlight beside the note's points or its head (`.note mark`).
struct SummaryMark: View {
    let text: String
    @Environment(\.meadow) private var meadow

    var body: some View {
        Text(text.uppercased())
            .font(.meadow(.ui, 10.5, .heavy))
            .tracking(0.63)
            .foregroundStyle(meadow.sunInk)
            .padding(.horizontal, 6)
            .padding(.vertical, 3)
            .background(meadow.sunWash, in: RoundedRectangle(cornerRadius: 5))
            .fixedSize()
    }
}

/// The lead (`.lead`): what the message comes to, in moss.
private struct SummaryLead: View {
    let text: String
    @Environment(\.meadow) private var meadow

    var body: some View {
        Text(text)
            .font(.meadow(.ui, 15.5, .semibold))
            .lineSpacing(MeadowFace.ui.lineSpacing(size: 15.5, lineHeight: 1.6, weight: .semibold))
            .foregroundStyle(meadow.moss)
            .fixedSize(horizontal: false, vertical: true)
            .textSelection(.enabled)
    }
}

/// The points under the lead, each after a dot.
private struct SummaryPoints: View {
    let points: [String]
    @Environment(\.meadow) private var meadow

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            ForEach(Array(points.enumerated()), id: \.offset) { _, point in
                HStack(alignment: .top, spacing: 9) {
                    Circle().fill(meadow.moss.opacity(0.6)).frame(width: 5, height: 5).padding(.top, 9)
                    Text(point)
                        .font(.meadow(.ui, 15, .medium))
                        .lineSpacing(MeadowFace.ui.lineSpacing(size: 15, lineHeight: 1.5, weight: .medium))
                        .foregroundStyle(meadow.moss)
                        .fixedSize(horizontal: false, vertical: true)
                        .textSelection(.enabled)
                }
            }
        }
        .padding(.top, 8)
    }
}

/// What the message asks of you next (`.next`), with the reply written ahead to use when there is one.
private struct SummaryNext: View {
    let text: String
    let copy: MailCopy
    @Environment(\.meadow) private var meadow

    var body: some View {
        HStack(spacing: 10) {
            MeadowIconView(.check)
                .foregroundStyle(meadow.ink2)
            Text(text)
                .font(.meadow(.ui, 14, .bold))
                .foregroundStyle(meadow.ink)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .textSelection(.enabled)
            if Composing.shared.chips[copy.key] == .ready {
                Button { Task { await Composing.shared.answer(copy) } } label: {
                    MeadowIconView(.wind, size: 15)
                    Text("Use draft")
                }
                .meadowButton(.ai, small: true)
                .help("The reply MailMind wrote ahead, to read and change before you send it")
            }
        }
        .padding(.vertical, 9)
        .padding(.leading, 12)
        .padding(.trailing, 10)
        .background(meadow.paperRaised.opacity(0.78), in: RoundedRectangle(cornerRadius: 12))
        .padding(.top, 10)
    }
}

/// The note's foot (`.ft`): whose words these are not, and Not quite right? to have them written again.
private struct SummaryFoot: View {
    let author: String?
    let help: String
    let rerun: () -> Void
    @Environment(\.meadow) private var meadow

    var body: some View {
        // Wrapped as the page's foot is: where both don't fit on a line, Not quite right? goes under the words.
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                words.fixedSize()
                Spacer(minLength: 4)
                again
            }
            VStack(alignment: .leading, spacing: 4) {
                words.fixedSize(horizontal: false, vertical: true)
                again
            }
        }
        .padding(.top, 8)
    }

    private var words: some View {
        Text(SummaryCard.foot(author))
            .font(.meadow(.ui, 12))
            .foregroundStyle(meadow.ink3)
    }

    private var again: some View {
        Button("Not quite right?", action: rerun)
            .buttonStyle(ReaderLinkStyle())
            .focusEffectDisabled()
            .help(help)
    }
}

extension SummaryCard {
    /// "Not Ren's words — the message is the source.": the sender's first name, as the mockup says it.
    static func foot(_ author: String?) -> String {
        let first = author?.split(separator: " ").first.map(String.init)?.trimmingCharacters(in: .punctuationCharacters)
        guard let first, !first.isEmpty, !first.contains("@") else {
            return String(localized: "Not the sender’s words — the message is the source.")
        }
        return String(localized: "Not \(first)’s words — the message is the source.")
    }
}

/// A summary that couldn't be written, and Retry.
private struct SummaryFailed: View {
    let why: String
    let retry: () -> Void
    @Environment(\.meadow) private var meadow

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(why.isEmpty ? "Couldn’t summarize." : "Couldn’t summarize — \(why).")
                .font(.meadow(.ui, 13.5, .semibold))
                .foregroundStyle(meadow.rust)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 4)
            Button("Retry", action: retry)
                .buttonStyle(ReaderLinkStyle())
                .focusEffectDisabled()
        }
    }
}
#endif
