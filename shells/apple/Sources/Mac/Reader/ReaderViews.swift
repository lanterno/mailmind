#if os(macOS)
import AppKit
import SwiftUI

/// The reader's reading settings (Aa) as the server keeps them, with what each is with nothing said.
struct ReadingPrefs: Equatable {
    var width: API.ReadWidth = .medium
    var look = MailLook.Prefs()
    var conversations = true

    init() {}

    init(_ preferences: API.Preferences?) {
        guard let preferences else { return }
        width = preferences.readWidth
        look = MailLook.Prefs(colors: preferences.mailColors, font: preferences.readFont, size: preferences.readSize)
        conversations = preferences.conversations
    }
}

/// What stands around a body, then the body, as the page's `MailContent`: who it's really from, a Reply-To that
/// would take an answer elsewhere, the cards, the remote images held back, the files, and the frame. Asking for
/// images loads the frame again with them, and nothing else.
struct MessageContent: View {
    let core: Core
    let copy: MailCopy
    let opened: API.MessageOpened
    let context: ReaderContext
    /// The least the frame is before its content has measured, and what it adds below the content.
    var minHeight: CGFloat = 400
    var pad: CGFloat = 30
    let prefs: ReadingPrefs
    let mine: Set<String>
    @State private var images = false
    @State private var height: CGFloat?
    @Environment(\.colorScheme) private var scheme

    private var blocked: Int { images ? 0 : opened.blockedImages + opened.blockedCssUrls }
    private var senderDomain: String? {
        opened.fromAddr.flatMap { $0.split(separator: "@").last.map { String($0).lowercased() } }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ExternalBar(domain: senderDomain, blocked: blocked)
            if let note = ReplyGoesElsewhere.text(opened, mine: mine) {
                Label(note, systemImage: "exclamationmark.triangle.fill")
                    .font(.callout)
                    .foregroundStyle(.orange)
                    .accessibilityAddTraits(.isStaticText)
            }
            ReaderCards(placement: .message, context: context)
            if blocked > 0 {
                HStack(spacing: 8) {
                    Image(systemName: "shield.lefthalf.filled")
                    Text("\(blocked) remote image\(blocked > 1 ? "s" : "") hidden — loading one can let the sender know you opened this email")
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 8)
                    Button("Load Images") {
                        images = true
                        height = nil
                    }
                }
                .font(.callout)
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(.yellow.opacity(0.14), in: RoundedRectangle(cornerRadius: 6))
            }
            let files = AttachmentsRow.shown(opened.attachments)
            if !files.isEmpty {
                AttachmentsRow(core: core, copy: copy, files: files)
            }
            frame
        }
    }

    private var frame: some View {
        let look = ReaderPalette.look(html: opened.hasHtml, prefs: prefs.look, scheme: scheme)
        let url = ReaderModel.frameURL(copy, images: images)
        let shown = height.map { $0 > 0 ? $0 + pad : minHeight + pad } ?? minHeight
        return Group {
            if let url {
                MailFrame(core: core, url: url, look: look, html: opened.hasHtml,
                          title: "Email body — from \(opened.fromName ?? opened.fromAddr ?? "sender"): \(opened.subject ?? "(no subject)")",
                          open: { link in
                              LinkOpener.open(link, senderDomain: senderDomain, in: NSApp.keyWindow)
                          },
                          measured: { measured in
                              if abs((height ?? -1) - measured) >= 1 { height = measured }
                          })
                    .id("\(url.absoluteString)\n\(look.css)")
                    .frame(height: min(shown, MailFrame.tallest))
            }
        }
    }
}

extension MailFrame {
    /// The tallest a frame grows to fit its content: past it, the frame scrolls its own.
    static let tallest: CGFloat = 50_000
}

/// The line over a message: where it's from, and what its frame keeps out.
struct ExternalBar: View {
    let domain: String?
    let blocked: Int

    var body: some View {
        HStack(spacing: 12) {
            Text("External\(domain.map { " · \($0)" } ?? "")")
            Label(blocked > 0 ? "Sandboxed · \(blocked) tracker\(blocked > 1 ? "s" : "") blocked" : "Sandboxed · no active content",
                  systemImage: "checkmark.shield")
        }
        .font(.caption)
        .foregroundStyle(.secondary)
    }
}

/// A Reply-To that would take an answer out of the sender's organisation, said above the frame, where nothing in
/// the mail can draw over it.
enum ReplyGoesElsewhere {
    static func text(_ opened: API.MessageOpened, mine: Set<String>) -> String? {
        guard opened.isDraft != true, let redirect = Addresses.redirect(replyTo: opened.replyTo, sender: opened.fromAddr),
              !SendGuard.isMine(redirect.to, mine), !SendGuard.isMine(redirect.sender, mine),
              LinkRisk.leavesOrganisation(redirect.to, redirect.sender) else { return nil }
        return GuardWarning.redirect(to: redirect.to, sender: redirect.sender, block: .soft).line().text
    }
}

/// Who a message is from, to whom and when, as the reader's head shows it.
struct MessageHeader: View {
    let opened: API.MessageOpened
    var showsSubject = true

    private var name: String {
        [opened.fromName, opened.fromAddr].compactMap { $0 }.first { !$0.isEmpty } ?? ""
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if showsSubject {
                Text(opened.subject.flatMap { $0.isEmpty ? nil : $0 } ?? "(no subject)")
                    .font(.title2.weight(.semibold))
                    .textSelection(.enabled)
                    .accessibilityAddTraits(.isHeader)
            }
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(name).font(.headline)
                if let address = opened.fromAddr, !address.isEmpty, address != name {
                    Text(address).foregroundStyle(.secondary)
                }
                Spacer(minLength: 12)
                Text(opened.date).foregroundStyle(.secondary).font(.callout)
            }
            .textSelection(.enabled)
            line("To", opened.to)
            if !opened.cc.isEmpty { line("Cc", opened.cc) }
        }
    }

    @ViewBuilder private func line(_ label: LocalizedStringKey, _ value: String) -> some View {
        if !value.isEmpty {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(label).foregroundStyle(.secondary)
                Text(value).lineLimit(2).textSelection(.enabled)
            }
            .font(.callout)
        }
    }
}

/// The quick replies under a message from a person you could answer.
struct QuickReplies: View {
    let copy: MailCopy

    var body: some View {
        HStack(spacing: 8) {
            Label("Quick reply", systemImage: "arrowshape.turn.up.left")
                .font(.callout)
                .foregroundStyle(.secondary)
            ForEach(QuickReply.all, id: \.key) { quick in
                Button(quick.chip) { Task { await Composing.shared.quickReply(copy, quick) } }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Quick replies")
    }
}

/// What a body says while it comes: the first open of an older message goes to the server, and can take a while.
struct Fetching: View {
    var whole = true
    @State private var slow = false

    var body: some View {
        VStack(spacing: 10) {
            ProgressView()
            if slow {
                Text(whole ? "Still fetching from the server — the first open of an older message can take up to a minute."
                     : "Still fetching this message…")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            }
        }
        .frame(maxWidth: .infinity, minHeight: 120)
        .task {
            try? await Task.sleep(for: .seconds(4))
            slow = true
        }
        .accessibilityLabel("Loading")
    }
}
#endif
