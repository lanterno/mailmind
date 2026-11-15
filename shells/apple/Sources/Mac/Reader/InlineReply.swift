#if os(macOS)
import AppKit
import SwiftUI

// The reply under a message (#44's `.ireply`): closed, a line to start a reply in, the reply MailMind wrote ahead
// when there is one, and Forward; open, the reply written right there on the composer the window uses, sent only
// by Send, through the same send guard. Pop out takes it to a window of its own as it stands.

/// Who a reply goes to and what its box says before it opens.
struct ReplyBoxWords {
    /// The person answered, as the reader names them, and their address.
    let name: String
    let address: String?
    /// In Sent: the box follows up rather than answers.
    let following: Bool

    var firstName: String {
        let first = name.split(separator: " ").first.map(String.init) ?? ""
        return first.isEmpty || first.contains("@") ? (address ?? name) : first
    }

    var placeholder: String {
        following ? String(localized: "Follow up with \(firstName)…") : String(localized: "Reply to \(firstName)…")
    }

    /// "Drafted by MailMind from the 3-message thread."
    static func drafted(messages: Int) -> String {
        let from = messages > 1 ? String(localized: "the \(messages)-message thread")
            : String(localized: "the message")
        let freely = String(localized: "Edit freely — nothing is sent until you press Send.")
        return String(localized: "Drafted by MailMind from \(from).") + " " + freely
    }
}

/// The box: closed, or the reply open in it.
struct ReplyBox: View {
    /// The message a reply answers.
    let copy: MailCopy
    let words: ReplyBoxWords
    /// The account it came to, whose name the box's avatar and its From say.
    let me: String
    /// How many messages the conversation has, for what a drafted reply was read from.
    var messages = 1
    /// Whether quick replies are offered: a person you could answer.
    var quick = false
    private var composing: Composing { .shared }

    var body: some View {
        Group {
            if let open = composing.inline, open.copy == copy, !open.composer.closed {
                OpenReply(composer: open.composer, words: words, drafted: open.drafted, messages: messages)
            } else {
                ClosedReply(copy: copy, words: words, me: me, quick: quick,
                            ready: composing.chips[copy.key] == .ready)
            }
        }
        .padding(.top, 26)
        .onAppear { composing.offerInline(copy) }
        .onDisappear { composing.withdrawInline(copy) }
    }
}

extension Composing {
    /// The reader shows a reply box for `copy`.
    func offerInline(_ copy: MailCopy) {
        slotTurn += 1
        inlineSlot = copy
    }

    /// The reader's box for `copy` is gone. A view drawn again (focus reading, a narrower pane) offers it again at
    /// once; only one gone for good gives up the slot, which closes a reply left open there and saves it.
    func withdrawInline(_ copy: MailCopy) {
        let turn = slotTurn
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { [weak self] in
            guard let self, self.slotTurn == turn, self.inlineSlot == copy else { return }
            self.inlineSlot = nil
        }
    }
}

/// The closed box (`.ireply`): the line that opens a reply, R, the drafted reply, Forward, and the quick replies.
private struct ClosedReply: View {
    let copy: MailCopy
    let words: ReplyBoxWords
    let me: String
    let quick: Bool
    let ready: Bool
    @Environment(\.meadow) private var meadow

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Button { Task { await Composing.shared.reply(copy) } } label: {
                    HStack(spacing: 10) {
                        MeadowAvatar(name: me, size: .small)
                        Text(words.placeholder)
                            .font(.meadow(.ui, 14))
                            .frame(maxWidth: .infinity, alignment: .leading)
                        MeadowKey("R")
                    }
                }
                .buttonStyle(ReplyLineStyle())
                .focusEffectDisabled()
                .frame(minWidth: 200)
                .accessibilityLabel(words.placeholder)
                .accessibilityHint("Opens a reply here")
                if ready {
                    Button { Task { await Composing.shared.answer(copy) } } label: {
                        MeadowIconView(.wind, size: 16)
                        Text(words.following ? "Draft a nudge" : "Use the drafted reply")
                    }
                    .meadowButton(.ai)
                    .help("The reply MailMind wrote ahead, to read and change before you send it")
                }
                Button { Task { await Composing.shared.forward(copy) } } label: {
                    MeadowIconView(.forward, size: 16)
                    Text("Forward")
                }
                .meadowButton(.quiet)
            }
            if quick {
                QuickReplies(copy: copy)
            }
        }
        .padding(10)
        .background(meadow.paperRaised, in: RoundedRectangle(cornerRadius: 16))
        .overlay { RoundedRectangle(cornerRadius: 16).strokeBorder(meadow.line, lineWidth: 1) }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Reply")
    }
}

/// The line a reply starts from (`.ireply-ph`): paper, a shade deeper under the pointer.
private struct ReplyLineStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        ReplyLine(label: configuration.label)
    }
}

private struct ReplyLine: View {
    let label: ButtonStyleConfiguration.Label
    @Environment(\.meadow) private var meadow
    @Environment(\.isFocused) private var focused
    @State private var hovering = false

    var body: some View {
        label
            .foregroundStyle(meadow.ink3)
            .padding(.horizontal, 10)
            .frame(minHeight: 40)
            .background(hovering ? meadow.paper3 : meadow.paper2, in: RoundedRectangle(cornerRadius: 10))
            .contentShape(RoundedRectangle(cornerRadius: 10))
            .onHover { hovering = $0 }
            .meadowFocusRing(focused, radius: 10)
    }
}

/// The reply open in the box (`.ireply.open`): to whom and from which account, Pop out and discard, the note on a
/// drafted reply, the text in the reading face, the rewrites, then Send (and Send later), attach, and whether it's
/// saved.
private struct OpenReply: View {
    let composer: Composer
    let words: ReplyBoxWords
    let drafted: Bool
    let messages: Int
    @State private var translating = false
    @State private var language = ""
    @Environment(\.meadow) private var meadow
    @Environment(\.meadowLight) private var light

    private var composing: Composing { .shared }

    /// The rewrites the box offers, as the mockup names them.
    private static let rewrites: [(op: API.RewriteOp, label: LocalizedStringKey)] = [
        (.shorten, "Shorter"), (.friendly, "Warmer"), (.formal, "More formal"), (.fix, "Fix grammar"),
        (.translate, "Translate"),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            head
            Rectangle().fill(meadow.line).frame(height: 1)
            if drafted {
                HStack(spacing: 8) {
                    MeadowIconView(.wind, size: 16)
                    Text(ReplyBoxWords.drafted(messages: messages))
                        .fixedSize(horizontal: false, vertical: true)
                }
                .font(.meadow(.ui, 13))
                .foregroundStyle(meadow.moss)
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(meadow.mossWash, in: RoundedRectangle(cornerRadius: 10))
                .padding(.horizontal, 12)
                .padding(.top, 10)
            }
            text
            if let check = composer.check {
                GuardStrip(composer: composer, check: check)
            }
            if composing.aiOn { rewriteRow }
            foot
        }
        .background(meadow.paperRaised)
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .overlay { RoundedRectangle(cornerRadius: 16).strokeBorder(meadow.line, lineWidth: 1) }
        .background {
            MeadowShadowsOutside(shadows: [.init(x: 0, y: 18, blur: 40, spread: -26,
                                                 color: Color(red: 30 / 255, green: 50 / 255, blue: 90 / 255,
                                                              opacity: 0.5), inset: false)],
                                 radius: 16)
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Reply to \(words.name)")
    }

    /// To whom, from which account, Pop out and discard (`.ir-h`).
    private var head: some View {
        HStack(spacing: 8) {
            let to = composer.to.first?.value ?? ""
            let name = to.isEmpty || to.caseInsensitiveCompare(words.address ?? "") == .orderedSame ? words.name : to
            HStack(spacing: 6) {
                MeadowAvatar(name: name, size: .small)
                Text(verbatim: composer.to.count > 1 ? "\(name) +\(composer.to.count - 1)" : name)
                    .font(.meadow(.ui, 13, .bold))
                    .foregroundStyle(meadow.ink)
                    .lineLimit(1)
            }
            .padding(.leading, 3)
            .padding(.trailing, 10)
            .frame(height: 28)
            .background(meadow.paper3, in: Capsule())
            .accessibilityElement(children: .combine)
            .accessibilityLabel("To \(name)")
            Text("from \(ComposerForm.alias(composer.from, composing.digest?.aliases ?? [:]))")
                .font(.meadow(.ui, 13))
                .foregroundStyle(meadow.ink3)
                .lineLimit(1)
            Spacer(minLength: 8)
            Button { composing.popOut() } label: {
                MeadowIconView(.external, size: 14)
                Text("Pop out")
            }
            .meadowButton(.quiet, small: true)
            .help("Go on writing in a window of its own")
            Button { Task { await composer.discard() } } label: { MeadowIconView(.x, size: 14) }
                .meadowButton(.quiet, small: true, icon: true)
                .accessibilityLabel("Discard reply")
                .help("Discard this reply, and its draft")
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
    }

    /// The text (`.ir-body`), as the compose dialog sets a message's: the reading face, its lines 1.65 apart.
    private var text: some View {
        let look = ComposeTextLook(meadow, light: light)
        return ComposeTextView(composer: composer, text: composer.body, selectionRequest: composer.selectionRequest,
                               focusBody: composer.focusRequest == .body, look: look,
                               commandReturn: { send(held: $0) })
            .frame(height: max(190, ComposeTextLook.size * ComposeTextLook.lineHeight * 7 + 24))
            .overlay(alignment: .topLeading) {
                if composer.body.isEmpty {
                    Text("Write your reply…")
                        .font(Font(look.font))
                        .foregroundStyle(meadow.ink3)
                        .padding(.horizontal, ComposeTextLook.inset.width)
                        .padding(.top, ComposeTextLook.inset.height)
                        .allowsHitTesting(false)
                }
            }
            .accessibilityLabel("Your reply")
    }

    /// Rewrite with MailMind (`.rw`): on the selection, or the whole reply.
    private var rewriteRow: some View {
        HStack(spacing: 4) {
            MeadowAIMark(composer.busy == .rewrite ? "Rewriting…" : "Rewrite")
                .padding(.leading, 4)
                .padding(.trailing, 6)
            ForEach(Self.rewrites, id: \.op) { rewrite in
                Button(rewrite.label) {
                    if rewrite.op == .translate {
                        translating = true
                    } else {
                        Task { await composer.rewrite(rewrite.op) }
                    }
                }
                .meadowButton(.quiet, small: true)
                .disabled(composer.busy != .none)
                .popover(isPresented: rewrite.op == .translate ? $translating : .constant(false), arrowEdge: .bottom) {
                    translateAsk
                }
            }
            if composer.undoRewrite != nil {
                Button("Undo") { composer.undoTheRewrite() }
                    .meadowButton(.quiet, small: true)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(meadow.paper2)
        .overlay(alignment: .top) { Rectangle().fill(meadow.line).frame(height: 1) }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Rewrite with MailMind (acts on the selection, or the whole reply)")
    }

    private var translateAsk: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Translate to which language?")
            TextField("Language", text: $language)
                .frame(width: 200)
                .onSubmit(translate)
            HStack {
                Spacer()
                Button("Cancel") { translating = false }
                    .keyboardShortcut(.cancelAction)
                Button("Translate", action: translate)
                    .keyboardShortcut(.defaultAction)
                    .disabled(language.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .padding(14)
    }

    private func translate() {
        let to = language.trimmingCharacters(in: .whitespaces)
        translating = false
        guard !to.isEmpty else { return }
        Task { await composer.rewrite(.translate, language: to) }
    }

    /// Send, Send later, attach, and whether it's saved (`.ir-f`).
    private var foot: some View {
        HStack(spacing: 8) {
            MeadowSplitButton(kind: .primary, action: send, label: {
                MeadowIconView(.send, size: 16)
                Text(composer.check != nil && composer.check?.hard == false ? "Send anyway" : "Send")
                MeadowKey("⌘↵")
            }, moreLabel: String(localized: "Send later"), more: {
                composer.laterOpen.toggle()
            })
            .popover(isPresented: Binding(get: { composer.laterOpen }, set: { composer.laterOpen = $0 }),
                     arrowEdge: .top) {
                LaterChoices(composer: composer)
            }
            .help("Send · ⌘Return while you write")
            Button(action: attach) { MeadowIconView(.clip, size: 16) }
                .meadowButton(.quiet, icon: true)
                .accessibilityLabel("Attach a file")
                .help("Attach a file")
            if !composer.attachments.isEmpty {
                Text(composer.attachments.count == 1 ? "1 file" : "\(composer.attachments.count) files")
                    .font(.meadow(.ui, 12.5, .semibold))
                    .foregroundStyle(meadow.ink2)
            }
            Spacer(minLength: 8)
            Text(composer.notice ?? (composer.savedLine.isEmpty ? String(localized: "Saves once you type")
                                                                  : composer.savedLine))
                .font(.meadow(.ui, 12.5))
                .foregroundStyle(meadow.ink3)
                .lineLimit(1)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .overlay(alignment: .top) { Rectangle().fill(meadow.line).frame(height: 1) }
    }

    /// Send: through the send guard, as the window's Send; a check shown is passed only where it may be, and never by
    /// a press that only repeats one (⌘Return held).
    private func send() {
        send(held: NSEvent.repeatingAPress)
    }

    private func send(held: Bool) {
        composer.pressSend(held: held)
    }

    private func attach() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false
        panel.prompt = String(localized: "Attach")
        let take = { [composer] (answer: NSApplication.ModalResponse) in
            guard answer == .OK else { return }
            composer.add(panel.urls.map { .file($0) })
        }
        if let window = NSApp.keyWindow {
            panel.beginSheetModal(for: window, completionHandler: take)
        } else {
            take(panel.runModal())
        }
    }
}
#endif
