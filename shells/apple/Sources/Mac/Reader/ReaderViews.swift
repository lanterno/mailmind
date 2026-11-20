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

/// The messages whose remote images a person asked to see, for as long as MailMind is open: what the reader's frame
/// loads them with, wherever the message shows (the pane, a conversation, its own window), and what File › Print puts
/// on paper, so a ticket's code prints as it shows.
@MainActor
@Observable
final class ShownImages {
    static let shared = ShownImages()
    private var shown: Set<MailCopy> = []

    func contains(_ copy: MailCopy) -> Bool { shown.contains(copy) }

    func show(_ copy: MailCopy) { shown.insert(copy) }
}

/// What stands under a message's head, then the body, as the page's `MailContent` and Meadow's `.msg` order them:
/// the trust line (where it's from, that its frame is a sandbox, the remote images held back a click away), a Reply-To
/// that would take an answer elsewhere, the invite, the body on the pane's paper, and its files. Asking for images
/// loads the frame again with them, and nothing else.
struct MessageContent: View {
    let core: Core
    let copy: MailCopy
    let opened: API.MessageOpened
    let context: ReaderContext
    /// The least the frame is before its content has measured.
    var minHeight: CGFloat = 400
    let prefs: ReadingPrefs
    let mine: Set<String>
    var shownImages = ShownImages.shared
    @State private var height: CGFloat?
    @Environment(\.meadow) private var meadow
    @Environment(\.meadowLight) private var light
    @Environment(\.linkHover) private var linkHover

    private var images: Bool { shownImages.contains(copy) }
    private var blocked: Int { images ? 0 : opened.blockedImages + opened.blockedCssUrls }
    private var senderDomain: String? {
        opened.fromAddr.flatMap { $0.split(separator: "@").last.map { String($0).lowercased() } }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            TrustLine(from: Self.from(opened, mine: mine), blocked: blocked,
                      loadImages: blocked > 0 ? {
                          shownImages.show(copy)
                          height = nil
                      } : nil)
                .padding(.top, 12)
            if let note = ReplyGoesElsewhere.text(opened, mine: mine) {
                TrustWarning(text: note).padding(.top, 8)
            }
            if opened.isDraft == true, let item = Composing.shared.scheduledSend(draftId: opened.draftId) {
                ScheduledLine(copy: copy, item: item).padding(.top, 8)
            }
            ReaderCards(placement: .message, context: context)
            frame
                .padding(.top, 20)
            let files = AttachmentsRow.shown(opened.attachments)
            if !files.isEmpty {
                AttachmentsRow(core: core, copy: copy, files: files)
                    .padding(.top, 18)
            }
        }
    }

    /// Where the trust line says the mail is from: its sender, as the server's facts on them have it; for your own
    /// mail, the people it went to, of whom the server says nothing.
    static func from(_ opened: API.MessageOpened, mine: Set<String>) -> MessageWords.From {
        let side = MessageWords.otherSide(opened, mine: mine)
        return side.wentTo ? MessageWords.from(side.address, facts: nil)
            : MessageWords.from(opened.fromAddr, facts: opened.sender)
    }

    private var frame: some View {
        let look = ReaderPalette.look(html: opened.hasHtml, prefs: prefs.look, palette: meadow, dark: light.isDark)
        let url = ReaderModel.frameURL(copy, images: images)
        // A sheet of the mail's own has its margins inside the frame; mail on the pane's paper has none.
        let pad: CGFloat = look.sheet ? 30 : 4
        let shown = height.map { $0 > 0 ? $0 + pad : minHeight + pad } ?? minHeight
        return Group {
            if let url {
                MailFrame(source: .core(core), url: url, look: look, html: opened.hasHtml,
                          title: "Email body — from \(opened.fromName ?? opened.fromAddr ?? "sender"): \(opened.subject ?? "(no subject)")",
                          open: { link in
                              LinkOpener.open(link, senderDomain: senderDomain, in: NSApp.keyWindow)
                          },
                          measured: { measured in
                              if abs((height ?? -1) - measured) >= 1 { height = measured }
                          },
                          hovered: { linkHover?.url = $0 })
                    .id("\(url.absoluteString)\n\(look.css)\n\(look.paper ?? "")")
                    .frame(height: min(shown, MailFrame.tallest))
                    .clipShape(RoundedRectangle(cornerRadius: look.sheet ? 12 : 0))
            }
        }
    }
}

extension MailFrame {
    /// The tallest a frame grows to fit its content: past it, the frame scrolls its own.
    static let tallest: CGFloat = 50_000
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

/// The quick replies, in the reply box under a message from a person you could answer: each opens the reply with
/// its line.
struct QuickReplies: View {
    let copy: MailCopy

    var body: some View {
        // The chips wrap onto more lines in a narrow reader, as the page's do.
        FlowLayout(spacing: 6) {
            ForEach(QuickReply.all, id: \.key) { quick in
                Button(quick.chip) { Task { await Composing.shared.quickReply(copy, quick) } }
                    .meadowButton(.quiet, small: true)
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
    @Environment(\.meadow) private var meadow

    var body: some View {
        VStack(spacing: 10) {
            ProgressView()
                .controlSize(.small)
            if slow {
                Text(whole ? "Still fetching from the server — the first open of an older message can take up to a minute."
                     : "Still fetching this message…")
                    .font(.meadow(.ui, 13))
                    .foregroundStyle(meadow.ink3)
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
/// Where the link the pointer is over in a body goes, while it is over one, for the status line at the foot of what
/// reads it: the reading pane, or a message's own window.
@MainActor
@Observable
final class LinkHover {
    var url: URL?

    /// The words the line says: a web address whole, a mail address as the address alone.
    static func words(_ url: URL) -> String {
        guard url.scheme?.lowercased() == "mailto" else { return url.absoluteString }
        let address = url.absoluteString.dropFirst("mailto:".count).split(separator: "?").first.map(String.init) ?? ""
        return address.removingPercentEncoding ?? address
    }
}

extension EnvironmentValues {
    /// What reads a body keeps its status line here; none where a body is drawn away from one.
    @Entry var linkHover: LinkHover?
}

extension View {
    /// A status line at the foot of this, as a Mac's browser keeps one: where the link the pointer is over in a body
    /// inside goes, before anything opens it.
    func linkStatusLine() -> some View {
        modifier(LinkStatusLine())
    }
}

private struct LinkStatusLine: ViewModifier {
    @State private var hover = LinkHover()

    func body(content: Content) -> some View {
        content
            .environment(\.linkHover, hover)
            .overlay(alignment: .bottomLeading) { LinkStatus(hover: hover) }
    }
}

/// The line itself: the address in the paper's raised face, a hairline round it, cut in its middle when long; nothing
/// while the pointer is over no link. VoiceOver reads a link where it stands, so the line is for the eye only.
struct LinkStatus: View {
    let hover: LinkHover
    @Environment(\.meadow) private var meadow

    var body: some View {
        if let url = hover.url {
            Text(verbatim: LinkHover.words(url))
                .font(.meadow(.ui, 12))
                .foregroundStyle(meadow.ink2)
                .lineLimit(1)
                .truncationMode(.middle)
                .padding(.horizontal, 10)
                .padding(.vertical, 5)
                .background(meadow.paperRaised, in: RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(meadow.line))
                .frame(maxWidth: 560, alignment: .leading)
                .padding(10)
                .allowsHitTesting(false)
                .accessibilityHidden(true)
        }
    }
}
#endif
