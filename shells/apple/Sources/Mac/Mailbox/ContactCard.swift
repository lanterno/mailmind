#if os(macOS)
import SwiftUI

/// Who a sender is (discovery/ContactCard.tsx): how much they've written, whether you answer, the accounts they write
/// to, their latest mail, what the AI can say of them on a click, and two ways on: write to them, or their mail.
struct ContactCard: View {
    let mailbox: Mailbox
    let address: String
    @Environment(\.dismiss) private var dismiss
    @State private var card: API.ContactCard?
    @State private var failed = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let card {
                head(card)
                Text(stats(card)).font(.callout).foregroundStyle(.secondary)
                if card.accounts.count > 1 {
                    Text("writes to \(card.accounts.map(mailbox.name(of:)).joined(separator: ", "))")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
                if !card.recent.isEmpty {
                    VStack(alignment: .leading, spacing: 2) {
                        ForEach(card.recent, id: \.self) { mail in
                            Button {
                                dismiss()
                                mailbox.chose([])
                                mailbox.app?.open(MailCopy(key: mail.key, folder: mail.folder, uid: mail.uid, account: mail.account))
                            } label: {
                                HStack {
                                    Text(mail.subject).fontWeight(mail.seen ? .regular : .semibold).lineLimit(1)
                                    Spacer()
                                    Text(mail.when).foregroundStyle(.secondary)
                                }
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                            .padding(.vertical, 3)
                        }
                    }
                }
                SenderSummary(mailbox: mailbox, address: card.addr)
                HStack {
                    if let compose = mailbox.composeTo {
                        Button("Compose", systemImage: "square.and.pencil") {
                            dismiss()
                            compose(card.addr)
                        }
                    }
                    Button("From This Sender", systemImage: "magnifyingglass") {
                        dismiss()
                        mailbox.app?.place = .search(Search(q: "from:\(card.addr)"))
                    }
                    Spacer()
                    Button("Done") { dismiss() }
                        .keyboardShortcut(.defaultAction)
                }
            } else if failed {
                Text("Couldn’t load this sender.")
                HStack { Spacer(); Button("Done") { dismiss() } }
            } else {
                ProgressView("Loading…").frame(maxWidth: .infinity)
            }
        }
        .padding(20)
        .frame(width: 420)
        .task(id: address) { await read() }
        .accessibilityLabel("About this sender")
    }

    private func head(_ card: API.ContactCard) -> some View {
        HStack(spacing: 12) {
            let hue = Double(Monogram.hue(card.addr)) / 360
            Text(Monogram.initials(name: card.name, address: card.addr))
                .font(.title3.weight(.semibold))
                .foregroundStyle(.white)
                .frame(width: 44, height: 44)
                .background(Color(hue: hue, saturation: 0.38, brightness: 0.68), in: .circle)
                .accessibilityHidden(true)
            VStack(alignment: .leading) {
                Text(card.name.isEmpty ? card.addr : card.name).font(.title3.weight(.semibold))
                Text(card.addr).font(.callout).foregroundStyle(.secondary).textSelection(.enabled)
            }
        }
    }

    private func stats(_ card: API.ContactCard) -> String {
        [Words.sender(count: card.count, replied: card.replied, firstAgo: card.firstAgo),
         card.lastAgo.map { "last \($0) ago" } ?? ""].filter { !$0.isEmpty }.joined(separator: " · ")
    }

    private func read() async {
        guard let calls = mailbox.calls else { return }
        do {
            card = try await calls.get(API.Get.Contact.self, ["addr": address])
        } catch {
            failed = true
        }
    }
}

/// What the AI can say of a sender's recent threads, on a click and never without one (`Summarize`): it spends.
private struct SenderSummary: View {
    let mailbox: Mailbox
    let address: String
    @State private var asking = false
    @State private var said: API.SenderSummary?
    @State private var refused: String?
    /// The server said the AI is off, which a person turns on in Settings.
    @State private var off = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if asking {
                ProgressView("Reading their recent threads…").controlSize(.small)
            } else if let said {
                Text(said.summary)
                if !said.topics.isEmpty {
                    Text(said.topics.joined(separator: " · ")).font(.callout).foregroundStyle(.secondary)
                }
                if !said.open.isEmpty {
                    Text("\(Text("Still with you:").bold()) \(said.open)").font(.callout)
                }
            } else if !mailbox.aiOn || off {
                Text("AI is off.").font(.callout).foregroundStyle(.secondary)
            } else {
                if let refused { Text(refused).font(.callout).foregroundStyle(.secondary) }
                Button(refused == nil ? "Summarize This Sender" : "Try Again", systemImage: "wind") { summarize() }
            }
        }
    }

    private func summarize() {
        guard let calls = mailbox.calls else { return }
        asking = true
        refused = nil
        Task {
            defer { asking = false }
            do {
                said = try await calls.post(API.Post.ContactSummary.self, API.SenderSummaryBody(addr: address),
                                            asking: calls.asksOverLimit)
            } catch let problem as Problem {
                off = problem.more["ai_off"] == .bool(true)
                refused = problem.isRefusal ? problem.detail ?? problem.title : String(localized: "Couldn’t summarize this sender.")
            } catch {
                refused = String(localized: "Couldn’t summarize this sender.")
            }
        }
    }
}
#endif
