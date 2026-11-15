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

/// The answer, in the list's place, with the mail it cites. Opening a source leaves the answer on screen.
struct AskScreen: View {
    let model: AskModel
    /// The copy the reader has open, so its source shows as being read.
    let reading: MailCopy?
    let open: (MailCopy) -> Void
    let askAgain: () -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                switch model.asking {
                case nil:
                    ContentUnavailableView("Ask your mail", systemImage: "sparkles",
                                           description: Text("Ask a question from the Assistant menu."))
                case .reading(let question):
                    DeskCard {
                        heading(question)
                        DeskWaitingBars(lines: 4, saying: "Reading your inbox…")
                    }
                case .failed(let question, let why):
                    DeskCard {
                        heading(question)
                        HStack(alignment: .firstTextBaseline) {
                            Text("Couldn’t answer: \(why)").foregroundStyle(.red)
                            Button("Retry") { Task { await model.ask(question) } }.buttonStyle(.link)
                        }
                        .font(.callout)
                    }
                case .answered(let question, let answer):
                    answered(question, answer)
                }
            }
            .padding(16)
        }
    }

    private func heading(_ question: String) -> some View {
        DeskCardHeading(title: "Answer", symbol: "sparkles") {
            Text(question).font(.callout).foregroundStyle(.secondary).lineLimit(2)
        }
    }

    @ViewBuilder private func answered(_ question: String, _ answer: API.Answered) -> some View {
        DeskCard {
            heading(answer.question.isEmpty ? question : answer.question)
            Text(answer.answer).textSelection(.enabled)
            DeskCardFoot {
                Text("\(answer.model.isEmpty ? "claude" : answer.model) · grounded in your mail")
                Spacer()
                Button("Ask Again", systemImage: "arrow.clockwise") { Task { await model.ask(question) } }
                    .buttonStyle(.borderless)
                Button("New Question…", action: askAgain).buttonStyle(.borderless)
            }
        }
        if answer.sources.isEmpty {
            Text("No specific message cited.").font(.callout).foregroundStyle(.secondary)
        } else {
            Text("\(answer.sources.count) cited sources")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
                .textCase(.uppercase)
            VStack(spacing: 6) {
                ForEach(answer.sources, id: \.self) { source in
                    let copy = MailCopy.named(key: source.key, folder: source.folder, uid: source.uid, account: source.account)
                    SourceRow(source: source, reading: reading == copy) { open(copy) }
                }
            }
        }
    }
}

/// A message the answer cites: who, which account, its subject and the start of it.
private struct SourceRow: View {
    let source: API.AskSource
    let reading: Bool
    let open: () -> Void

    var body: some View {
        Button(action: open) {
            VStack(alignment: .leading, spacing: 2) {
                Text(verbatim: [source.from ?? "", Self.short(source.account)].filter { !$0.isEmpty }.joined(separator: " · "))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text(source.subject.isEmpty ? String(localized: "(no subject)") : source.subject)
                    .font(.callout.weight(.medium))
                    .lineLimit(1)
                Text(String(source.snippet.prefix(140)))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(10)
            .background(reading ? AnyShapeStyle(.selection) : AnyShapeStyle(.quinary),
                        in: RoundedRectangle(cornerRadius: 8))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityHint("Opens the message")
    }

    /// An address as the page shortens one: the part before the @.
    static func short(_ address: String) -> String {
        address.split(separator: "@").first.map(String.init) ?? address
    }
}

/// The question, asked in a sheet from the Assistant menu or the palette.
struct AskSheet: View {
    let ask: (String) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var question = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("Ask Your Mail", systemImage: "sparkles").font(.headline)
            TextField("When is the dentist appointment?", text: $question, axis: .vertical)
                .lineLimit(1...4)
                .textFieldStyle(.roundedBorder)
                .onSubmit(submit)
            Text("Claude reads your mail to answer and cites what it read. Nothing is changed.")
                .font(.caption)
                .foregroundStyle(.secondary)
            HStack {
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button("Ask", action: submit)
                    .keyboardShortcut(.defaultAction)
                    .disabled(question.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(20)
        .frame(width: 440)
    }

    private func submit() {
        let asked = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !asked.isEmpty else { return }
        dismiss()
        ask(asked)
    }
}
#endif
