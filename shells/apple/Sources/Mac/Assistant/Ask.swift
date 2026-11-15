#if os(macOS)
import Observation
import SwiftUI

/// A question asked of your mail (`/api/ai/ask`), as the page's "?…" asks it: Claude answers from your mail and
/// cites what it read, and nothing changes. A newer question takes the place of one still being answered.
@MainActor
@Observable
final class AskModel {
    enum Asking: Equatable {
        case reading(question: String)
        case failed(question: String, why: String)
        case answered(question: String, API.Answered)

        var question: String {
            switch self {
            case .reading(let question), .failed(let question, _), .answered(let question, _): question
            }
        }
    }

    private(set) var asking: Asking?
    @ObservationIgnored private var seq = 0
    @ObservationIgnored let link: CoreLink

    init(link: CoreLink) {
        self.link = link
    }

    /// Ask `question` of every account's mail, or `account`'s.
    func ask(_ question: String, account: String = "all") async {
        let question = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let routes = link.routes, !question.isEmpty else { return }
        seq += 1
        let mine = seq
        asking = .reading(question: question)
        do {
            let answer = try await routes.post(API.Post.AiAsk.self, API.AskBody(account: account, question: question),
                                               asking: link.asksOverLimit)
            guard mine == seq else { return }
            asking = .answered(question: question, answer)
            let count = answer.sources.count
            AccessibilityNotification.Announcement(String(localized: "Answer ready — \(count) cited sources")).post()
        } catch {
            guard mine == seq else { return }
            let why = DeskRefusal.isAIOff(error) ? String(localized: "AI is off")
                : DeskRefusal.isOverLimit(error) ? String(localized: "today’s AI limit is reached")
                : String(localized: "ask failed")
            asking = .failed(question: question, why: why)
        }
    }

    func stop() {
        seq += 1
        asking = nil
    }
}

/// The answer, in the list's place, as Meadow's palette shows one (`.answer`): on a moss wash, what you asked, the
/// answer in the AI's voice, and the mail it cites as numbered rows (`.cite`). Opening a source leaves the answer on
/// screen.
struct AskScreen: View {
    let model: AskModel
    /// The copy the reader has open, so its source shows as being read.
    let reading: MailCopy?
    let open: (MailCopy) -> Void
    let askAgain: () -> Void
    @Environment(\.meadow) private var meadow

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                switch model.asking {
                case nil:
                    DeskEmpty(icon: .wind, title: String(localized: "Ask your mail"), words: Self.nothingAsked)
                case .reading(let question):
                    AskAnswer(question: question) {
                        DeskThinking(saying: "Reading your mail…")
                            .padding(.top, 10)
                    }
                case .failed(let question, let why):
                    AskAnswer(question: question) {
                        Text("Couldn’t answer: \(why)")
                            .font(.meadow(.ui, 14.5, .semibold))
                            .foregroundStyle(meadow.rust)
                            .padding(.top, 8)
                        Button { Task { await model.ask(question) } } label: {
                            MeadowIconView(.refresh, size: 15)
                            Text("Retry")
                        }
                        .meadowButton(small: true)
                        .padding(.top, 10)
                    }
                case .answered(let question, let answer):
                    answered(question, answer)
                }
            }
            .padding(10)
        }
        .scrollContentBackground(.hidden)
    }

    @ViewBuilder private func answered(_ question: String, _ answer: API.Answered) -> some View {
        AskAnswer(question: answer.question.isEmpty ? question : answer.question) {
            Text(answer.answer)
                .font(.meadow(.ui, 16, .semibold))
                .foregroundStyle(meadow.moss)
                .lineSpacing(MeadowFace.ui.lineSpacing(size: 16, lineHeight: 1.6, weight: .semibold))
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
                .padding(.top, 8)
            if !answer.sources.isEmpty {
                VStack(spacing: 6) {
                    ForEach(Array(answer.sources.enumerated()), id: \.offset) { at, source in
                        let copy = MailCopy.named(key: source.key, folder: source.folder, uid: source.uid,
                                                  account: source.account)
                        SourceRow(number: at + 1, source: source, reading: reading == copy) { open(copy) }
                    }
                }
                .padding(.top, 12)
            }
            Text(Self.footnote(answer.sources.count))
                .font(.meadow(.ui, 12.5))
                .foregroundStyle(meadow.ink2)
                .padding(.top, 12)
        }
        HStack(spacing: 6) {
            Button { Task { await model.ask(question) } } label: {
                MeadowIconView(.refresh, size: 15)
                Text("Ask again")
            }
            .meadowButton(.quiet, small: true)
            .fixedSize()
            Button { askAgain() } label: {
                MeadowIconView(.wind, size: 15)
                Text("New question…")
            }
            .meadowButton(.quiet, small: true)
            .fixedSize()
            Spacer(minLength: 4)
            // Which model answered, where the column has room for it after the buttons.
            Text(answer.model.isEmpty ? "claude" : answer.model)
                .font(.meadow(.ui, 12))
                .foregroundStyle(meadow.ink3)
                .lineLimit(1)
                .layoutPriority(-1)
        }
        .padding(.horizontal, 6)
    }

    private static let nothingAsked = String(localized: """
        Type ? and a question in the search, or choose Ask Your Mail in the Assistant menu. \
        Claude answers from your mail and says what it read.
        """)

    /// What the answer read and left alone (the palette's line under the cites).
    static func footnote(_ cited: Int) -> String {
        switch cited {
        case 0: String(localized: "No message is cited. Nothing was changed.")
        case 1: String(localized: "Only the message above was read. Nothing was changed.")
        default: String(localized: "Only the messages above were read. Nothing was changed.")
        }
    }
}

/// An answer's card (`.answer`): grained moss wash, "You asked: …" in the quiet ink, the AI's mark, then what goes
/// under it.
private struct AskAnswer<Content: View>: View {
    let question: String
    @ViewBuilder let content: Content
    @Environment(\.meadow) private var meadow

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("You asked: “\(question)”")
                .font(.meadow(.ui, 13))
                .foregroundStyle(meadow.ink3)
                .lineLimit(3)
                .padding(.bottom, 8)
            MeadowAIMark(String(localized: "Answer from your mail"))
            content
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background {
            ZStack {
                meadow.mossWash
                MeadowGrain()
            }
            .clipShape(RoundedRectangle(cornerRadius: 14))
        }
        .accessibilityElement(children: .contain)
    }
}

/// A message the answer cites (`.cite`): its number on moss, who sent it and its subject, the start of it, and when.
private struct SourceRow: View {
    let number: Int
    let source: API.AskSource
    let reading: Bool
    let open: () -> Void
    @Environment(\.meadow) private var meadow
    @FocusState private var focused: Bool
    @State private var hovering = false

    var body: some View {
        let subject = source.subject.isEmpty ? String(localized: "(no subject)") : source.subject
        Button(action: open) {
            HStack(alignment: .center, spacing: 10) {
                Text("\(number)")
                    .font(.meadow(.ui, 11, .heavy))
                    .foregroundStyle(meadow.mossWash)
                    .frame(width: 22, height: 22)
                    .background(RoundedRectangle(cornerRadius: 6).fill(meadow.moss))
                VStack(alignment: .leading, spacing: 2) {
                    Text("\(Text(source.from ?? Self.short(source.account)).fontWeight(.bold)) — \(subject)")
                        .font(.meadow(.ui, 13))
                        .foregroundStyle(meadow.ink)
                        .lineLimit(2)
                    if !source.snippet.isEmpty {
                        Text(String(source.snippet.prefix(140)))
                            .font(.meadow(.ui, 12.5))
                            .foregroundStyle(meadow.ink3)
                            .lineLimit(2)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                Text(Self.when(source.date))
                    .font(.meadow(.ui, 12))
                    .foregroundStyle(meadow.ink3)
                    .lineLimit(1)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 9)
            .background(RoundedRectangle(cornerRadius: 10)
                .fill(reading ? meadow.primaryWash : hovering ? meadow.paper2 : meadow.paperRaised))
            .overlay {
                RoundedRectangle(cornerRadius: 10).strokeBorder(reading ? meadow.primary.opacity(0.4) : meadow.line)
            }
            .contentShape(RoundedRectangle(cornerRadius: 10))
        }
        .buttonStyle(.plain)
        .focused($focused)
        .focusEffectDisabled()
        .meadowFocusRing(focused, radius: 10)
        .onHover { hovering = $0 }
        .help(source.account)
        .accessibilityHint("Opens the message")
        .accessibilityAddTraits(reading ? .isSelected : [])
    }

    /// An address as the page shortens one: the part before the @.
    static func short(_ address: String) -> String {
        address.split(separator: "@").first.map(String.init) ?? address
    }

    /// "Nov 12" for the day a source was sent, as the server writes it ("2026-11-12"); anything else as it came.
    static func when(_ date: String, calendar: Calendar = .current, locale: Locale = .current) -> String {
        guard let day = SuggestedTime.day(String(date.prefix(10)), in: calendar.timeZone) else { return date }
        return day.formatted(Date.FormatStyle(locale: locale, calendar: calendar, timeZone: calendar.timeZone)
            .month(.abbreviated).day())
    }
}
#endif
