#if os(macOS)
import SwiftUI

/// The desk in the palette, as the mockup's palette has it: `?` and a question asks your inbox and shows the answer
/// there, with the mail it cites; `!` and a command plans a bulk action and shows what it would do. Each waits for
/// Return before it asks the AI anything. A question asked here opens the answer page behind the palette, as the
/// search field's `?` and Ask Your Mail… (⌥⌘A) always have, so the answer stays once the palette goes.
extension Desk {
    func paletteModes(undoKey: String) -> [Entries.Mode] {
        let asking = asking, plan = plan, showSearch = showSearch
        return [
            Entries.Mode(prefix: "?", card: { [weak self] words, close in
                AnyView(AskCard(model: asking, words: words) { copy in
                    // Opened before the palette goes, so the answer stays beside it (`WindowOverlays.leavesAnswer`).
                    self?.openCited(copy)
                    close()
                })
            }, submit: { [weak self] words, _ in
                guard AskCard.asks(words, now: asking.asking) else { return }
                Task { await self?.ask(words) }
            }),
            Entries.Mode(prefix: "!", card: { words, close in
                AnyView(PlanCard(model: plan, words: words, showSearch: showSearch, undoKey: undoKey, close: close))
            }, submit: { words, close in
                switch plan.shown(for: words) {
                case .plan?: plan.press(closing: close)
                case .search(_, let filter)?:
                    close()
                    showSearch?(filter)
                case .planning?: break
                default:
                    guard !words.isEmpty else { return }
                    Task { await plan.plan(words) }
                }
            }),
        ]
    }

    /// A message an answer cites, opened with the answer page in the list's place, as the answer page opens its own:
    /// the answer stays there beside it.
    func openCited(_ copy: MailCopy) {
        goToAnswer()
        open(copy)
    }
}

/// A question's answer in the palette (`.answer`): on moss's wash, the question, then the AI reading, or what it
/// answered from your mail and the messages it cites, each opening where it is.
struct AskCard: View {
    let model: AskModel
    /// What is typed after the `?`.
    let words: String
    let open: (MailCopy) -> Void
    @Environment(\.meadow) private var meadow

    /// Whether Return asks: something is typed, and it isn't already being answered or answered.
    static func asks(_ words: String, now: AskModel.Asking?) -> Bool {
        let words = words.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !words.isEmpty else { return false }
        switch now {
        case .reading(let question)?, .answered(let question, _)?: return question != words
        case .failed?, nil: return true
        }
    }

    var body: some View {
        let words = words.trimmingCharacters(in: .whitespacesAndNewlines)
        let asking = model.asking.flatMap { $0.question == words ? $0 : nil }
        VStack(alignment: .leading, spacing: 0) {
            if !words.isEmpty {
                Text(asking == nil ? String(localized: "You’re asking: “\(words)”")
                    : String(localized: "You asked: “\(words)”"))
                    .font(.meadow(.ui, 13))
                    .foregroundStyle(meadow.ink3)
                    .lineLimit(2)
                    .padding(.bottom, 8)
            }
            switch asking {
            case nil:
                MeadowAIMark(String(localized: "Ask your inbox"))
                Text(words.isEmpty ? String(localized: "Type a question, then press ↵.")
                    : String(localized: "Press ↵ to ask."))
                    .font(.meadow(.ui, 16, .semibold))
                    .foregroundStyle(meadow.moss)
                    .padding(.top, 8)
                note(String(localized: "The AI reads your mail to answer and cites what it read. Nothing is changed."))
            case .reading?:
                MeadowThinking(text: String(localized: "Reading your mail…"))
            case .failed(let question, let why)?:
                HStack(spacing: 10) {
                    Text("Couldn’t answer: \(why)")
                        .font(.meadow(.ui, 14, .semibold))
                        .foregroundStyle(meadow.rust)
                    Button("Retry") { Task { await model.ask(question) } }
                        .meadowButton(small: true)
                }
            case .answered(_, let answer)?:
                answered(answer)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 16)
        .padding(.horizontal, 18)
        .background {
            ZStack {
                meadow.mossWash
                MeadowGrain()
            }
            .clipShape(RoundedRectangle(cornerRadius: 14))
        }
        .padding(6)
    }

    @ViewBuilder private func answered(_ answer: API.Answered) -> some View {
        MeadowAIMark(String(localized: "Answer from your mail"))
        Text(answer.answer)
            .font(.meadow(.ui, 16, .semibold))
            .foregroundStyle(meadow.moss)
            .lineSpacing(MeadowFace.ui.lineSpacing(size: 16, lineHeight: 1.6, weight: .semibold))
            .fixedSize(horizontal: false, vertical: true)
            .textSelection(.enabled)
            .padding(.top, 8)
        if !answer.sources.isEmpty {
            VStack(spacing: 6) {
                ForEach(Array(answer.sources.enumerated()), id: \.offset) { index, source in
                    AskCite(number: index + 1, source: source) {
                        open(MailCopy.named(key: source.key, folder: source.folder, uid: source.uid,
                                            account: source.account))
                    }
                }
            }
            .padding(.top, 12)
        }
        note(answer.sources.isEmpty ? String(localized: "No message was cited. Nothing was changed.")
            : String(localized: "Answered from your mail. Nothing was changed."))
    }

    private func note(_ text: String) -> some View {
        Text(text)
            .font(.meadow(.ui, 12.5))
            .foregroundStyle(meadow.ink3)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.top, 10)
    }
}

/// A message the answer cites (`.cite`): its number on moss, who and what, and its day; it opens where it is.
struct AskCite: View {
    let number: Int
    let source: API.AskSource
    let open: () -> Void
    @Environment(\.meadow) private var meadow
    @State private var hovering = false
    @FocusState private var focused: Bool

    var body: some View {
        let who = source.from.flatMap { $0.isEmpty ? nil : $0 } ?? Self.short(source.account)
        let subject = source.subject.isEmpty ? String(localized: "(no subject)") : source.subject
        Button(action: open) {
            HStack(spacing: 10) {
                Text(verbatim: "\(number)")
                    .font(.meadow(.ui, 11, .heavy))
                    .foregroundStyle(meadow.mossWash)
                    .frame(width: 22, height: 22)
                    .background(RoundedRectangle(cornerRadius: 6).fill(meadow.moss))
                Text("\(Text(verbatim: who).bold()) — \(subject)")
                    .foregroundStyle(meadow.ink)
                    .lineLimit(1)
                Spacer(minLength: 0)
                Text(Self.day(source.date))
                    .font(.meadow(.ui, 12))
                    .foregroundStyle(meadow.ink3)
            }
            .font(.meadow(.ui, 13))
            .padding(.vertical, 9)
            .padding(.horizontal, 10)
            .background(RoundedRectangle(cornerRadius: 10).fill(hovering ? meadow.paper3 : meadow.paperRaised))
            .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(meadow.line, lineWidth: 1))
            .contentShape(RoundedRectangle(cornerRadius: 10))
        }
        .buttonStyle(.plain)
        .focused($focused)
        .focusEffectDisabled()
        .meadowFocusRing(focused, radius: 10)
        .onHover { hovering = $0 }
        .accessibilityHint("Opens the message")
    }

    /// An address as the page shortens one: the part before the @.
    static func short(_ address: String) -> String {
        address.split(separator: "@").first.map(String.init) ?? address
    }

    /// The day a source was sent, as the mockup's citations say it: the time today (or Today, for a day alone), else
    /// the day and the month, and the year when it isn't this one; the server's words where they aren't a date.
    static func day(_ date: String, now: Date = AppSeams.clock.now, calendar: Calendar = .current) -> String {
        let full = ISO8601DateFormatter()
        let plain = DateFormatter()
        plain.locale = Locale(identifier: "en_US_POSIX")
        plain.calendar = calendar
        plain.timeZone = calendar.timeZone
        plain.dateFormat = "yyyy-MM-dd"
        let dayAlone = date.count == 10
        guard let when = (dayAlone ? nil : full.date(from: date)) ?? plain.date(from: String(date.prefix(10))) else {
            return date
        }
        var style = Date.FormatStyle.dateTime
        style.calendar = calendar
        style.timeZone = calendar.timeZone
        if calendar.isDate(when, inSameDayAs: now) {
            return dayAlone ? String(localized: "Today") : when.formatted(style.hour().minute())
        }
        let sameYear = calendar.isDate(when, equalTo: now, toGranularity: .year)
        return when.formatted(sameYear ? style.weekday(.abbreviated).day().month(.abbreviated)
                                       : style.day().month(.abbreviated).year())
    }
}
#endif
