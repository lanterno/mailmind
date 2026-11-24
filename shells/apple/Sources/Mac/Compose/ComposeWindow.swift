#if os(macOS)
import AppKit
import SwiftUI
import UniformTypeIdentifiers

/// A composer in a window of its own, the Mac's way: several can be open at once, each with its own draft. The
/// window holds what `Composing` opened under its id, and closes once that composer has (sent, discarded, or
/// closed with what it held saved). It is drawn as Meadow's compose dialog (#44): the window is the dialog's paper,
/// with no title bar of its own, and its traffic lights stand in the dialog's head.
struct ComposeWindow: View {
    static let scene = "compose"
    let id: UUID?
    @Environment(\.dismiss) private var dismiss
    private var composing: Composing { .shared }

    var body: some View {
        if let id, let composer = composing.composers[id] {
            ComposerForm(composer: composer, host: composing, aliases: composing.digest?.aliases ?? [:],
                         accounts: composing.digest?.accounts ?? []) { tone in
                Task { await Composing.shared.change(API.ChangedPreferences(draftTone: tone)) }
            }
            .ignoresSafeArea()
            .meadowWindowChrome(barHeight: ComposerForm.headHeight)
            .navigationTitle(ComposerForm.title(composer.mode))
            // The Message menu's Send Later… acts on the composer whose window is key.
            .keyWindowShows(.composer(composer))
            // The window closed: what's unsaved is saved, as the page's close does. Sending or discarding closed
            // the composer first, and closing twice does nothing.
            .onDisappear { composer.close() }
        } else {
            Color.clear
                .frame(width: 1, height: 1)
                .onAppear { dismiss() }
        }
    }
}

/// What the composer's window shows, as the mockup's compose dialog (`.compose`) lays it out over the page's
/// `compose/Composer.tsx`: the head, From, the recipients with completion, the subject, the AI's row, the text or
/// its preview, the quote, the files, the send guard's lines, and the foot with Send.
struct ComposerForm: View {
    let composer: Composer
    let host: any ComposeHost
    let aliases: [String: String?]
    /// The accounts in the config's order, which gives each its colour.
    var accounts: [String] = []
    /// Whether the window's traffic lights stand at the head's left; a test's picture has none.
    var lights = true
    let chooseTone: (API.DraftTone) -> Void

    @FocusState private var subjectFocused: Bool
    @State private var quoteOpen = false
    @State private var snippetsShown = false
    @State private var draftOptions = false
    @State private var translating = false
    @State private var choosingFrom = false
    @State private var fromMenu = MeadowMenuModel()
    @State private var language = "Spanish"
    @State private var dropping = false
    @Environment(\.meadow) private var meadow
    @Environment(\.meadowLight) private var light
    @Environment(\.dismiss) private var dismiss

    /// The head's height: the close button's 34 and its padding, 14 above and 10 below. The traffic lights are
    /// centred on the button, so the title bar is grown to twice the button's middle.
    static let headHeight: CGFloat = 2 * (14 + 17)

    /// The window's title, which the Window menu lists.
    static func title(_ mode: API.ComposeMode) -> String {
        switch mode {
        case .new: "New Message"
        case .reply: "Reply"
        case .replyall: "Reply All"
        case .forward: "Forward"
        }
    }

    /// The head's words, as the mockup's dialog says them: "New message", or "Reply to" and the first name of whom
    /// it answers, as its chip names them or else the message replied to does (`names`).
    static func heading(_ mode: API.ComposeMode, to: [Recipient], names: [String: String] = [:]) -> String {
        switch mode {
        case .new: return String(localized: "New message")
        case .forward: return String(localized: "Forward")
        case .reply, .replyall:
            guard let value = to.first?.value, case let first = ChipField.shown(value), !first.isEmpty else {
                return mode == .reply ? String(localized: "Reply") : String(localized: "Reply all")
            }
            let address = Addresses.recipient(value)
            let named = Addresses.name(value) ?? names[address] ?? first
            // A person by their first name, an organisation whole (`Words.calling`), else the address.
            let name = Words.calling(named, address: address) ?? first
            return String(localized: "Reply to \(name)")
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            head
            if !composer.note.isEmpty { noteLine }
            fields
                .zIndex(1)   // a completion list hangs over the text below it
            if host.aiOn { aiRow }
            writing
                // The text takes the room the rest leave, down to its least: the recipient rows stop growing at a
                // few lines and scroll inside, then give way down to a line each, so the head and Send stay in
                // the window however many people a message is to.
                .layoutPriority(-1)
            if let check = composer.check {
                GuardStrip(composer: composer, check: check)
            }
            foot
        }
        .frame(minWidth: 640, idealWidth: 760, minHeight: 460, idealHeight: 600)
        .background { ComposePaper().ignoresSafeArea() }
        .overlay(alignment: .bottom) { noticeView }
        .overlay {
            if dropping {
                RoundedRectangle(cornerRadius: 14).strokeBorder(meadow.focus, lineWidth: 2).padding(6)
                    .allowsHitTesting(false)
            }
        }
        .background { shortcuts }
        .onDrop(of: [.fileURL], isTargeted: $dropping) { providers in
            Self.urls(from: providers) { urls in composer.add(urls.map { .file($0) }) }
            return true
        }
        .onChange(of: composer.focusRequest) { _, wanted in place(wanted) }
        .onChange(of: composer.wantsFiles) { _, wanted in
            if wanted, composer.takeFilesRequest() { choose(pictures: false) }
        }
        .onAppear {
            let first = composer.focusRequest ?? firstPlace
            if first == .subject {
                // A fix in the reader's reply box brought the reply here for its subject: the field takes the keys
                // once the window it opened is up.
                Task { place(first) }
            } else {
                place(first)
            }
            // Asked for before this window was drawn, the panel would wait for a change that never comes.
            if composer.takeFilesRequest() { choose(pictures: false) }
        }
    }

    /// Where the first keys land: a new message starts at To, anything else in the text.
    private var firstPlace: Composer.Focus {
        composer.mode == .new && composer.draftId == nil && composer.to.isEmpty ? .field(.to) : .body
    }

    /// A place the composer asked for. The text and the recipient fields take theirs themselves.
    private func place(_ wanted: Composer.Focus?) {
        switch wanted {
        case .subject?:
            subjectFocused = true
            composer.focusRequest = nil
        case .schedule?:
            composer.focusRequest = nil
        case .guardStrip?, .send?, .attach?:
            composer.focusRequest = nil
        case .body?, .field?:
            if composer.focusRequest != wanted { composer.focusRequest = wanted }
        case nil:
            break
        }
    }

    /// Send and Save Draft's keys. Send's button is Meadow's split one, whose parts are its own; the keys live here,
    /// out of sight, so ⌘Return sends and ⌘S saves wherever the keys are in the window.
    private var shortcuts: some View {
        HStack {
            Button("Send") { send() }
                .keyboardShortcut(.return, modifiers: .command)
            Button("Save Draft") { _ = composer.save() }
                .keyboardShortcut("s")
        }
        .opacity(0)
        .frame(width: 0, height: 0)
        .accessibilityHidden(true)
    }

    // MARK: The head

    private var head: some View {
        HStack(spacing: 8) {
            Text(Self.heading(composer.mode, to: composer.to, names: composer.names))
                .font(.meadow(.display, 18, .bold))
                .foregroundStyle(meadow.ink)
                .lineLimit(1)
                .frame(maxWidth: .infinity, alignment: .leading)
                .accessibilityAddTraits(.isHeader)
            Button {
                if composer.previewing { composer.write() } else { Task { await composer.showPreview() } }
            } label: {
                MeadowIconView(.eye)
            }
            .meadowButton(.quiet, icon: true, pressed: composer.previewing)
            .help(composer.previewing ? "Back to writing" : "See how your message will read")
            .accessibilityLabel(composer.previewing ? "Back to writing" : "Preview")
            Button { dismiss() } label: { MeadowIconView(.x) }
                .meadowButton(.quiet, icon: true)
                .help("Close — your draft is kept")
                .accessibilityLabel("Close — your draft is kept")
        }
        .padding(.leading, lights ? WindowLights.room : 16)
        .padding(.trailing, 16)
        .padding(.top, 14)
        .padding(.bottom, 10)
        .background { WindowDragArea() }
    }

    /// What reopening a draft changed in it, said once above its fields.
    private var noteLine: some View {
        HStack(alignment: .top, spacing: 8) {
            MeadowIconView(.info, size: 15).padding(.top, 1)
            Text(composer.note).fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .font(.meadow(.ui, 12.5))
        .foregroundStyle(meadow.ink3)
        .padding(.horizontal, 12)
        .padding(.vertical, 7)
        .background(meadow.paper2, in: RoundedRectangle(cornerRadius: 10))
        .padding(.horizontal, 16)
        .padding(.bottom, 10)
    }

    // MARK: The fields

    private var fields: some View {
        VStack(spacing: 0) {
            ComposeFieldRow("From") { fromChoice }
            ComposeFieldRow("To") {
                HStack(alignment: .top, spacing: 6) {
                    ChipField(composer: composer, field: .to, label: "To", routes: host.routes)
                    if !composer.showCc {
                        Button("Cc Bcc") {
                            composer.showCopies()
                            composer.focusRequest = .field(.cc)
                        }
                        .meadowButton(.quiet, small: true)
                        .padding(.top, 3)
                        .help("Copy someone in")
                    }
                }
            }
            // Each recipient row stands over the rows after it, which its completion list hangs over.
            .zIndex(3)
            if composer.showCc {
                ComposeFieldRow("Cc") { ChipField(composer: composer, field: .cc, label: "Cc", routes: host.routes) }
                    .zIndex(2)
                ComposeFieldRow("Bcc") { ChipField(composer: composer, field: .bcc, label: "Bcc", routes: host.routes) }
                    .zIndex(1)
            }
            ComposeFieldRow("Subject") {
                TextField(text: Binding(get: { composer.subject }, set: { composer.setSubject($0) }),
                          prompt: Text(verbatim: "")) { Text("Subject") }
                    .textFieldStyle(.plain)
                    .font(.meadow(.ui, 14.5))
                    .foregroundStyle(meadow.ink)
                    .frame(minHeight: 34)
                    .focused($subjectFocused)
                    .accessibilityLabel(Text("Subject"))
            }
        }
    }

    /// From (`.fromsel`): the account's colour, its name and address, and with more than one account to send from, a
    /// chevron that lists the others.
    @ViewBuilder
    private var fromChoice: some View {
        let senders = host.senders
        // One account to send from has nothing to choose.
        Button { if senders.count > 1 { choosingFrom.toggle() } } label: {
            HStack(spacing: 8) {
                Circle().fill(colour(of: composer.from)).frame(width: 8, height: 8)
                Text(Self.fromWords(composer.from, aliases)).lineLimit(1)
                if senders.count > 1 { MeadowIconView(.down, size: 15) }
            }
            .font(.meadow(.ui, 13.5, .bold))
            .foregroundStyle(meadow.ink)
            .padding(.horizontal, 11)
            .frame(height: 32)
            .background(meadow.paper, in: RoundedRectangle(cornerRadius: 9))
            .overlay(RoundedRectangle(cornerRadius: 9).strokeBorder(meadow.line))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .fixedSize()
        .accessibilityLabel("From")
        .accessibilityValue(composer.from)
        .meadowPopover(isPresented: $choosingFrom, keys: { fromMenu.key($0) }) {
            MeadowMenu(parts: [.title(String(localized: "Send from"))] + senders.map { address in
                .item(MeadowMenuItem(id: address, title: Self.alias(address, aliases), detail: address,
                                     leading: AnyView(Circle().fill(colour(of: address)).frame(width: 8, height: 8)
                                        .frame(width: 18, height: 18)),
                                     check: address == composer.from) { composer.setFrom(address) })
            }, model: fromMenu, close: { choosingFrom = false }, label: String(localized: "Send from"))
        }
        .onChange(of: choosingFrom) { _, shown in if shown { fromMenu.at = 0 } }
        Spacer(minLength: 0)
    }

    /// An account's colour, by its place in the config.
    private func colour(of address: String) -> Color {
        meadow.account(accounts.firstIndex(of: address) ?? 0)
    }

    /// An account's name as the window shows it: its alias, or the part before the @.
    static func alias(_ address: String, _ aliases: [String: String?]) -> String {
        if let named = aliases[address] ?? nil, !named.isEmpty { return named }
        return String(address.split(separator: "@", omittingEmptySubsequences: false).first ?? "")
    }

    /// From's words: the account's alias and its address, as "Work · hana@kumo.studio", or the address alone.
    static func fromWords(_ address: String, _ aliases: [String: String?]) -> String {
        if let named = aliases[address] ?? nil, !named.isEmpty { return "\(named) · \(address)" }
        return address
    }

    // MARK: The AI's row

    /// The AI's row (`.rw`), in moss: the rewrites, which act on the selection or the whole message, and the reply
    /// drafted when there is one to draft.
    private var aiRow: some View {
        HStack(spacing: 4) {
            MeadowAIMark(composer.busy == .rewrite ? "Rewriting…" : "Rewrite")
                .padding(.leading, 4)
                .padding(.trailing, 6)
            ForEach(API.RewriteOp.offered, id: \.self) { op in
                Button(op.label) {
                    if op == .translate {
                        translating = true
                    } else {
                        Task { await composer.rewrite(op) }
                    }
                }
                .meadowButton(.quiet, small: true)
                .disabled(composer.busy != .none)
                .popover(isPresented: op == .translate ? $translating : .constant(false), arrowEdge: .bottom) {
                    translateAsk
                }
            }
            if composer.undoRewrite != nil {
                Button { composer.undoTheRewrite() } label: {
                    MeadowIconView(.undo, size: 15)
                    Text("Undo")
                }
                .meadowButton(.quiet, small: true)
                .help("Put back what the rewrite changed")
            }
            Spacer(minLength: 8)
            if composer.aiOffered { draftButtons }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .frame(minHeight: 40)
        .background(meadow.paper2)
        .overlay(alignment: .top) { ComposeHairline() }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("AI rewrite (acts on the selection, or the whole message)")
    }

    private var translateAsk: some View {
        ComposePop(title: String(localized: "Translate to")) {
            VStack(alignment: .leading, spacing: 10) {
                TextField("Language", text: $language)
                    .meadowComposeField()
                    .frame(width: 220)
                    .onSubmit(translate)
                HStack(spacing: 6) {
                    Spacer()
                    Button("Cancel") { translating = false }
                        .meadowButton(small: true)
                        .keyboardShortcut(.cancelAction)
                    Button("Translate", action: translate)
                        .meadowButton(.ai, small: true)
                        .keyboardShortcut(.defaultAction)
                        .disabled(language.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
            .padding(.horizontal, 10)
            .padding(.bottom, 6)
        }
    }

    private func translate() {
        let to = language.trimmingCharacters(in: .whitespaces)
        translating = false
        guard !to.isEmpty else { return }
        Task { await composer.rewrite(.translate, language: to) }
    }

    private var draftButtons: some View {
        let tone = host.tone
        let drafting = composer.busy == .draft
        return HStack(spacing: 2) {
            Button {
                Task { await composer.draftReply() }
            } label: {
                MeadowIconView(.wind, size: 15)
                Text(drafting ? "Drafting…" : tone == .balanced ? "Draft" : "Draft · \(tone.label.lowercased())")
            }
            .meadowButton(.ai, small: true)
            .help("Draft a reply with AI, \(tone.label.lowercased()) tone")
            Button {
                draftOptions.toggle()
            } label: {
                MeadowIconView(.down, size: 15)
            }
            .meadowButton(.ai, small: true, icon: true)
            .accessibilityLabel("Draft options")
            .popover(isPresented: $draftOptions, arrowEdge: .top) {
                DraftOptions(tone: tone, chooseTone: chooseTone) { instruction in
                    draftOptions = false
                    Task { await composer.draftReply(instruction: instruction) }
                }
            }
        }
        .fixedSize()
        .disabled(drafting)
    }

    // MARK: The text

    /// The least the text is given in a short window: a line, and its margin above.
    static var textLeast: CGFloat {
        ComposeTextLook.inset.height + (ComposeTextLook.size * ComposeTextLook.lineHeight).rounded(.up) + 6
    }

    @ViewBuilder
    private var writing: some View {
        VStack(alignment: .leading, spacing: 10) {
            if composer.previewing {
                if let preview = composer.preview {
                    if !preview.formatted {
                        Text("No Markdown here: this goes out as plain text.")
                            .font(.meadow(.ui, 12.5))
                            .foregroundStyle(meadow.ink3)
                            .padding(.horizontal, 18)
                            .padding(.top, 10)
                    }
                    QuoteFrame(html: preview.html, title: "How your message will read", takesKeys: true)
                        .padding(.horizontal, 4)
                } else {
                    ProgressView().controlSize(.small).frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            } else {
                ComposeTextView(composer: composer, text: composer.body, selectionRequest: composer.selectionRequest,
                                focusBody: composer.focusRequest == .body, look: ComposeTextLook(meadow, light: light),
                                commandReturn: { send(held: $0) })
                    .frame(minHeight: Self.textLeast)
                    .overlay(alignment: .topLeading) {
                        if composer.body.isEmpty {
                            Text("Write your message…")
                                .font(.meadow(.read, ComposeTextLook.size))
                                .foregroundStyle(meadow.ink3)
                                .padding(.leading, ComposeTextLook.inset.width)
                                .padding(.top, ComposeTextLook.inset.height)
                                .allowsHitTesting(false)
                                .accessibilityHidden(true)
                        }
                    }
            }
            quote
            files
        }
        .frame(maxHeight: .infinity)
    }

    @ViewBuilder
    private var quote: some View {
        if let quote = composer.quote, quote.html != nil || !quote.text.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Button { quoteOpen.toggle() } label: {
                    Text(Seeds.quoteLabel(mode: composer.mode, fwdAttach: composer.fwdAttach, open: quoteOpen))
                        .font(.meadow(.ui, 12, .bold))
                        .foregroundStyle(meadow.ink3)
                        .padding(.horizontal, 11)
                        .frame(height: 28)
                        .background(RoundedRectangle(cornerRadius: 14).fill(meadow.paperRaised))
                        .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(meadow.line2))
                        .contentShape(RoundedRectangle(cornerRadius: 14))
                }
                .buttonStyle(.plain)
                .accessibilityValue(quoteOpen ? "shown" : "hidden")
                if quoteOpen {
                    if let html = quote.html {
                        QuoteFrame(html: html, title: "The message you’re answering")
                            .frame(height: 240)
                            .clipShape(RoundedRectangle(cornerRadius: 12))
                            .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(meadow.line))
                    } else {
                        ScrollView {
                            Text(quote.text.isEmpty ? "(no preview)" : quote.text)
                                .font(.meadow(.read, 15))
                                .foregroundStyle(meadow.ink3)
                                .textSelection(.enabled)
                                .padding(.leading, 14)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .overlay(alignment: .leading) { Rectangle().fill(meadow.line2).frame(width: 2) }
                        }
                        .frame(maxHeight: 200)
                    }
                }
            }
            .padding(.horizontal, 18)
        }
    }

    @ViewBuilder
    private var files: some View {
        if !composer.attachments.isEmpty || !composer.reading.isEmpty {
            ChipFlow(spacing: 6, stretchesLast: false) {
                ForEach(Array(composer.attachments.enumerated()), id: \.offset) { index, file in
                    HStack(spacing: 6) {
                        MeadowIconView(file.cid != nil ? .image : .clip, size: 15).foregroundStyle(meadow.ink3)
                        Text(file.filename).lineLimit(1).foregroundStyle(meadow.ink)
                        if file.cid != nil { Text("in the text").foregroundStyle(meadow.ink3) }
                        Text(Words.size(file.size)).foregroundStyle(meadow.ink3)
                        Button {
                            composer.removeAttachment(at: index)
                        } label: {
                            MeadowIconView(.x, size: 13)
                        }
                        .buttonStyle(.plain)
                        .foregroundStyle(meadow.ink3)
                        .accessibilityLabel("Remove \(file.filename)")
                    }
                    .font(.meadow(.ui, 13, .bold))
                    .padding(.leading, 10)
                    .padding(.trailing, 9)
                    .frame(height: 28)
                    .background(Capsule().fill(meadow.paper3))
                }
                ForEach(composer.reading) { reading in
                    HStack(spacing: 6) {
                        ProgressView().controlSize(.mini)
                        Text("Reading \(reading.name)…")
                    }
                    .font(.meadow(.ui, 13))
                    .foregroundStyle(meadow.ink3)
                    .frame(height: 28)
                }
            }
            .padding(.horizontal, 18)
            .padding(.bottom, 10)
        }
    }

    // MARK: The foot

    private var sendLabel: String {
        if let check = composer.check, !check.hard { return check.sendAfter > 0 ? "Schedule Anyway" : "Send Anyway" }
        return composer.pendingAfter > 0 ? "Schedule" : "Send"
    }

    private var sendHelp: String {
        if let check = composer.check {
            if check.hard { return "Fix the line above first" }
            return check.sendAfter > 0 ? "Schedule for \(SendTimes.words(check.sendAfter)) anyway (⌘Return)"
                : "Send anyway (⌘Return)"
        }
        return composer.pendingAfter > 0 ? "Schedule for \(SendTimes.words(composer.pendingAfter)) (⌘Return)"
            : "Send (⌘Return)"
    }

    /// Nothing goes without the person pressing Send: the button, or its key. A press that only repeats one (⌘Return
    /// held, which comes here as often as the key repeats) never sends.
    private func send() {
        send(held: NSEvent.repeatingAPress)
    }

    private func send(held: Bool) {
        composer.pressSend(held: held)
    }

    /// The foot's quiet words: a snippet Tab would bring in, else how the draft stands.
    static func footLine(savedLine: String, offer: String?) -> String {
        if let offer { return String(localized: "Tab brings in ;\(offer)") }
        return savedLine.isEmpty ? String(localized: "Saves once you type") : savedLine
    }

    private var foot: some View {
        HStack(spacing: 8) {
            MeadowSplitButton(kind: .primary, action: send, label: {
                MeadowIconView(.send)
                Text(sendLabel)
                MeadowKey("⌘↵")
            }, moreLabel: String(localized: "Send later"), more: { composer.laterOpen.toggle() }, help: sendHelp)
                .popover(isPresented: Binding(get: { composer.laterOpen }, set: { composer.laterOpen = $0 }),
                         arrowEdge: .top) {
                    LaterChoices(composer: composer)
                }
            Button { choose(pictures: false) } label: { MeadowIconView(.clip) }
                .meadowButton(.quiet, icon: true)
                .help("Attach files")
                .accessibilityLabel("Attach a file")
            Button { choose(pictures: true) } label: { MeadowIconView(.image) }
                .meadowButton(.quiet, icon: true)
                .help("Put a picture in the text")
                .accessibilityLabel("Put a picture in the text")
            Button("Snippets") { snippetsShown.toggle() }
                .meadowButton(.quiet, small: true)
                .help("Words you keep, to bring in with ;name and Tab")
                .popover(isPresented: $snippetsShown, arrowEdge: .top) {
                    SnippetsPanel(composer: composer, host: host) { snippetsShown = false }
                }
            Spacer(minLength: 8)
            Text(Self.footLine(savedLine: composer.savedLine, offer: composer.offer?.trigger))
                .font(.meadow(.ui, 12.5))
                .foregroundStyle(meadow.ink3)
                .lineLimit(1)
                .layoutPriority(-1)   // the first to give way when the window is narrow
            Button { Task { await composer.discard() } } label: { MeadowIconView(.trash) }
                .meadowButton(.quiet, del: true, icon: true)
                .help("Discard this message, and its draft")
                .accessibilityLabel("Discard")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .overlay(alignment: .top) { ComposeHairline() }
    }

    /// Files from a panel: attachments, or pictures for the text where the cursor is.
    private func choose(pictures: Bool) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false
        panel.prompt = pictures ? "Insert" : "Attach"
        if pictures {
            panel.allowedContentTypes = Pictures.types.compactMap { UTType(mimeType: $0) }
        }
        let take = { (answer: NSApplication.ModalResponse) in
            guard answer == .OK else { return }
            composer.add(panel.urls.map { .file($0) }, inText: pictures)
        }
        if let window = NSApp.keyWindow {
            panel.beginSheetModal(for: window, completionHandler: take)
        } else {
            take(panel.runModal())
        }
    }

    /// The files a drop holds, as their URLs, once all are read.
    static func urls(from providers: [NSItemProvider], then take: @escaping @MainActor ([URL]) -> Void) {
        Task { @MainActor in
            var urls: [URL] = []
            for provider in providers where provider.canLoadObject(ofClass: URL.self) {
                if let url = await withCheckedContinuation({ (done: CheckedContinuation<URL?, Never>) in
                    _ = provider.loadObject(ofClass: URL.self) { url, _ in done.resume(returning: url) }
                }), url.isFileURL {
                    urls.append(url)
                }
            }
            if !urls.isEmpty { take(urls) }
        }
    }

    /// What the composer was last told, for a moment, in the toast's ink over the foot.
    @ViewBuilder
    private var noticeView: some View {
        if let notice = composer.notice {
            Text(notice)
                .font(.meadow(.ui, 14, .semibold))
                .foregroundStyle(light.isDark ? Color(meadow: 0x1B2238) : Color(meadow: 0xF5EFE2))
                .padding(.horizontal, 16)
                .frame(minHeight: 44)
                .background(RoundedRectangle(cornerRadius: 14)
                    .fill(light.isDark ? Color(meadow: 0xEDE7D7) : Color(meadow: 0x24221E)))
                .shadow(color: .black.opacity(0.3), radius: 18, y: 12)
                .padding(.bottom, 72)
                .transition(.opacity)
                .allowsHitTesting(false)
        }
    }
}

/// A recipient field: its chips (`.rcpt`), what is being typed, and the addresses the server suggests for it.
struct ChipField: View {
    let composer: Composer
    let field: ComposeField
    let label: String
    let routes: (any Routes)?
    @State private var completion = RecipientCompletion()
    @State private var lookup: Task<Void, Never>?
    @State private var editing = false
    /// How tall the field stands on screen, which the list hangs below.
    @State private var height: CGFloat = 34
    /// How tall its chips and the typing field wrap to, which may be more than it shows.
    @State private var wrapped: CGFloat = 34
    @Environment(\.meadow) private var meadow

    /// How far round its 13-point cross a chip's remove button takes a press: about 21 points across.
    static let removeReach: CGFloat = 4
    /// A line of chips, and the most a field shows before it scrolls: four lines.
    static let line: CGFloat = 34
    static let most: CGFloat = 4 * line + 3 * 4
    /// The typing field's place in the scroll, which the field keeps in sight.
    private static let typing = "typing"

    /// What VoiceOver calls a chip's remove button: the address it takes away, and what the chip shows of it.
    static func removeLabel(_ recipient: Recipient, fresh: Bool) -> String {
        let address = recipient.bad ? recipient.value.trimmingCharacters(in: .whitespaces) : recipient.address
        let note = recipient.bad ? String(localized: ", not a valid address") : fresh ? String(localized: ", new to you") : ""
        return String(localized: "Remove \(address)") + note
    }

    /// A chip's words: the name a recipient was written with, else the address.
    static func shown(_ value: String) -> String {
        let trimmed = value.trimmingCharacters(in: .whitespaces)
        guard let open = trimmed.firstIndex(of: "<"), open > trimmed.startIndex else { return trimmed }
        let name = trimmed[..<open].trimmingCharacters(in: CharacterSet(charactersIn: " \"'"))
        return name.isEmpty ? Addresses.recipient(trimmed) : name
    }

    var body: some View {
        let chips = composer.recipients(field)
        // Many people scroll inside the field, which stands at most four lines tall and gives way to one in a short
        // window; the end, where the typing field is, stays in sight as chips are added.
        let shown = min(wrapped, Self.most)
        ScrollViewReader { scroller in
            ScrollView(.vertical) {
                flow(chips)
                    .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { wrapped = $0 }
            }
            .scrollBounceBehavior(.basedOnSize)
            .onChange(of: wrapped) { scroller.scrollTo(Self.typing, anchor: .bottom) }
            .onChange(of: height) { scroller.scrollTo(Self.typing, anchor: .bottom) }
            .onChange(of: editing) { _, now in if now { scroller.scrollTo(Self.typing, anchor: .bottom) } }
            .onAppear { scroller.scrollTo(Self.typing, anchor: .bottom) }
        }
        .frame(minHeight: min(shown, Self.line), idealHeight: shown, maxHeight: shown)
        .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { height = $0 }
        // The list hangs below the field, never over it or the rows above: the field's row stands over the rows
        // after it (`fields`).
        .overlay(alignment: .topLeading) {
            if completion.shown { suggestions.offset(y: height + 4) }
        }
    }

    private func flow(_ chips: [Recipient]) -> some View {
        ChipFlow(spacing: 4) {
            ForEach(Array(chips.enumerated()), id: \.offset) { index, recipient in
                chip(recipient, at: index)
            }
            RecipientInput(
                label: label,
                placeholder: field == .to && chips.isEmpty ? String(localized: "Name or address") : "",
                text: Binding(get: { composer.typed[field] ?? "" }, set: { value in
                    composer.setTyped(field, value)
                    lookUp(value)
                }),
                move: { completion.move($0) },
                take: take,
                commit: {
                    composer.commitChip(field, composer.typed[field] ?? "")
                    close()
                },
                backspaceWhenEmpty: {
                    if !chips.isEmpty { composer.removeChip(field, at: chips.count - 1) }
                },
                escape: {
                    lookup?.cancel()
                    return completion.escape()
                },
                focused: composer.focusRequest == .field(field),
                focusTaken: {
                    editing = true
                    if composer.focusRequest == .field(field) { composer.focusRequest = nil }
                },
                lostFocus: {
                    editing = false
                    close()
                    // Left by a click, as by Tab: what is typed becomes a chip, rather than stay behind as text. A
                    // click on a suggestion leaves the field editing, so takes it, not this.
                    let typed = composer.typed[field] ?? ""
                    if !typed.trimmingCharacters(in: .whitespaces).isEmpty { composer.commitChip(field, typed) }
                },
                ink: NSColor(meadow.ink),
                quiet: NSColor(meadow.ink3)
            )
            .frame(minHeight: 34)
            // Named on SwiftUI's side too, where VoiceOver meets the field.
            .accessibilityLabel(Text(label))
            .id(Self.typing)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func chip(_ recipient: Recipient, at index: Int) -> some View {
        let address = recipient.address
        let fresh = !recipient.bad && composer.isNew(address)
        let shown = Self.shown(recipient.value)
        return HStack(spacing: 6) {
            MeadowAvatar(name: shown, size: .small)
            Text(shown).lineLimit(1)
            if fresh {
                Text("new")
                    .font(.meadow(.ui, 10.5, .heavy))
                    .textCase(.uppercase)
                    .tracking(0.6)
                    .foregroundStyle(meadow.sunInk)
                    .padding(.horizontal, 5)
                    .padding(.vertical, 3)
                    .background(meadow.sunWash, in: RoundedRectangle(cornerRadius: 5))
                    .accessibilityLabel(", new to you")
            }
            Button {
                composer.removeChip(field, at: index)
            } label: {
                // A plain button is pressed only on what it draws, the cross's strokes: it takes a press a little
                // round them too, as it looks.
                MeadowIconView(.x, size: 13)
                    .contentShape(Rectangle().inset(by: -Self.removeReach))
            }
            .buttonStyle(.plain)
            .foregroundStyle(recipient.bad ? meadow.rust : meadow.ink3)
            .accessibilityLabel(Self.removeLabel(recipient, fresh: fresh))
        }
        .font(.meadow(.ui, 13, .bold))
        .foregroundStyle(recipient.bad ? meadow.rust : meadow.ink)
        .padding(.leading, 3)
        .padding(.trailing, 9)
        .frame(height: 28)
        .background(RoundedRectangle(cornerRadius: 14).fill(recipient.bad ? meadow.rustWash : meadow.paper3))
        .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(recipient.bad ? meadow.rust.opacity(0.4) : .clear))
        .padding(.vertical, 3)
        .help(fresh ? "No earlier mail with this address in MailMind" : recipient.bad ? "Not a valid address" : address)
    }

    /// The server's suggestions, as Meadow's popover lists its choices (`.pop`, `.pal-i`).
    private var suggestions: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(Array(completion.items.enumerated()), id: \.element.addr) { index, item in
                Button {
                    take(item)
                } label: {
                    HStack(spacing: 12) {
                        MeadowAvatar(name: item.name.isEmpty ? item.addr : item.name, size: .small)
                        VStack(alignment: .leading, spacing: 1) {
                            Text(item.name.isEmpty ? item.addr : item.name).foregroundStyle(meadow.ink)
                            if !item.name.isEmpty {
                                Text(item.addr).font(.meadow(.ui, 12.5)).foregroundStyle(meadow.ink3)
                            }
                        }
                        .lineLimit(1)
                        Spacer(minLength: 0)
                    }
                    .font(.meadow(.ui, 14))
                    .padding(.horizontal, 10)
                    .padding(.vertical, 4)
                    .frame(minHeight: 38)
                    .background(RoundedRectangle(cornerRadius: 10)
                        .fill(index == completion.at ? meadow.primaryWash : .clear))
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(index == completion.at ? .isSelected : [])
            }
        }
        .padding(6)
        .frame(minWidth: 280, alignment: .leading)
        .fixedSize()
        .meadowPanel(radius: 14, raised: true)
        .accessibilityLabel("Addresses for \(label)")
    }

    private func close() {
        lookup?.cancel()
        completion.close()
    }

    /// The server's suggestions for what's typed, a moment after the typing stops, without those already chosen.
    private func lookUp(_ value: String) {
        lookup?.cancel()
        let typed = value.trimmingCharacters(in: .whitespaces)
        guard !typed.isEmpty, let routes else { return close() }
        lookup = Task {
            try? await Task.sleep(for: .milliseconds(140))
            guard !Task.isCancelled,
                  let read = try? await routes.get(API.Get.Contacts.self, ["q": typed]),
                  !Task.isCancelled else { return }
            completion.show(read.items, for: composer.typed[field] ?? typed, holding: composer.recipients(field))
        }
    }

    /// Return or Tab: the suggestion highlighted, else what is typed, made a chip.
    private func take() -> RecipientCompletion.Taken {
        let typed = composer.typed[field] ?? ""
        let taken = completion.taken(typed: typed)
        switch taken {
        case .suggestion(let address):
            close()
            composer.commitChip(field, address)
        case .typed:
            composer.commitChip(field, typed)
            close()
        case .nothing:
            break
        }
        return taken
    }

    private func take(_ item: API.Suggestion) {
        close()
        composer.commitChip(field, RecipientCompletion.chip(item))
    }
}

/// Chips in lines, with the field after them taking what is left of its line (a line of its own when little is).
/// Without `stretchesLast`, every item keeps its own width, as the files do.
struct ChipFlow: Layout {
    var spacing: CGFloat = 4
    var fieldMin: CGFloat = 140
    var stretchesLast = true

    /// Each item's own size, the field's width its least.
    private func sizes(_ subviews: Subviews) -> [CGSize] {
        subviews.enumerated().map { index, view in
            var size = view.sizeThatFits(.unspecified)
            if stretchesLast && index == subviews.count - 1 { size.width = fieldMin }
            return size
        }
    }

    /// Every item on one line, each its own width: the flow's ideal width, which it asks of a width it isn't given.
    private func oneLine(_ subviews: Subviews) -> CGFloat {
        let widths = sizes(subviews).map(\.width)
        return widths.reduce(0, +) + spacing * CGFloat(max(0, widths.count - 1))
    }

    private func lines(_ width: CGFloat, _ subviews: Subviews) -> [(frame: CGRect, index: Int)] {
        var x: CGFloat = 0, y: CGFloat = 0, line: CGFloat = 0
        var placed: [(CGRect, Int)] = []
        for (index, measured) in sizes(subviews).enumerated() {
            let last = stretchesLast && index == subviews.count - 1
            var size = measured
            size.width = min(size.width, width)
            // A hair's leeway, so sums that come to the width exactly don't wrap by a rounding.
            if x > 0 && x + size.width > width + 0.5 {
                y += line + spacing
                x = 0
                line = 0
            }
            if last { size.width = max(fieldMin, width - x) }
            placed.append((CGRect(x: x, y: y, width: size.width, height: size.height), index))
            x += size.width + spacing
            line = max(line, size.height)
        }
        // Each item stands in the middle of its line's height.
        var lineHeights: [CGFloat: CGFloat] = [:]
        for (frame, _) in placed { lineHeights[frame.minY] = max(lineHeights[frame.minY] ?? 0, frame.height) }
        return placed.map { frame, index in
            let room = (lineHeights[frame.minY] ?? frame.height) - frame.height
            return (frame.offsetBy(dx: 0, dy: (room / 2).rounded()), index)
        }
    }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        // Given a width, the items wrap at it as `placeSubviews` places them, so the height said is the height they
        // take there. Given none, or no limit, they stand on one line at their own widths: a size that is finite.
        let width = proposal.width.flatMap { $0.isFinite ? $0 : nil } ?? oneLine(subviews)
        let placed = lines(width, subviews)
        return CGSize(width: width, height: placed.map(\.frame.maxY).max() ?? 0)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        for (frame, index) in lines(bounds.width, subviews) {
            subviews[index].place(at: CGPoint(x: bounds.minX + frame.minX, y: bounds.minY + frame.minY),
                                  proposal: ProposedViewSize(frame.size))
        }
    }
}

/// The send guard's strip: what holds this send, each line's fix, and the way past it when there is one, on the
/// sunlit wash Meadow keeps for what needs you.
struct GuardStrip: View {
    let composer: Composer
    let check: GuardView
    /// Where a line's fix is done, when not in the composer alone: the reader's reply box, which lacks some of the
    /// fields a fix asks for.
    var fixing: ((GuardFix, ComposeField?) -> Void)?
    @Environment(\.meadow) private var meadow

    var body: some View {
        let later = check.sendAfter > 0
        VStack(alignment: .leading, spacing: 8) {
            Text(later ? "Not scheduled yet" : "Not sent yet")
                .font(.meadow(.ui, 13.5, .heavy))
                .foregroundStyle(meadow.ink)
            ForEach(check.lines) { line in
                HStack(alignment: .firstTextBaseline, spacing: 9) {
                    MeadowIconView(line.soft ? .info : .alert, size: 15)
                        .foregroundStyle(line.soft ? meadow.ink3 : meadow.sunInk)
                        .alignmentGuide(.firstTextBaseline) { $0[.bottom] - 3 }
                    Text(line.text)
                        .foregroundStyle(line.soft ? meadow.ink3 : meadow.ink2)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 6)
                    if let fix = line.fix {
                        Button(fix.label) {
                            if let fixing { fixing(fix, line.field) } else { composer.fix(fix, field: line.field) }
                        }
                        .meadowButton(small: true)
                    }
                }
            }
            HStack(spacing: 6) {
                Text(check.hard ? "Esc keeps editing" : "⌘Return \(later ? "schedules" : "sends") anyway · Esc keeps editing")
                    .font(.meadow(.ui, 12))
                    .foregroundStyle(meadow.ink3)
                Spacer()
                Button("Keep Editing") { composer.dismissGuard() }
                    .meadowButton(small: true)
                    .keyboardShortcut(.cancelAction)
                if !check.hard {
                    Button(later ? "Schedule Anyway" : "Send Anyway") {
                        composer.confirmGuard(repeated: NSEvent.repeatingAPress)
                    }
                    .meadowButton(.primary, small: true)
                    .disabled(!check.armed)
                }
            }
        }
        .font(.meadow(.ui, 13))
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .background(meadow.sunWash, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(meadow.sun.opacity(0.45)))
        .padding(.horizontal, 16)
        .padding(.bottom, 10)
        .accessibilityElement(children: .contain)
        .accessibilityLabel(later ? "Not scheduled yet" : "Not sent yet")
    }
}

/// Send later's choices, made as it opens so "in 1 hour" means an hour from now however long the window has been
/// open: Meadow's popover (`.pop`), each with its key.
struct LaterChoices: View {
    let composer: Composer
    @State private var custom = Date().addingTimeInterval(3600)
    /// The preset the keyboard is on: the first as the popover opens, as the page's `useFocusFirst` puts it there,
    /// never the date and time of one's own; the arrows move it and Return picks it.
    @FocusState private var focused: Int?
    /// The date and time of one's own has the keyboard: its digits, arrows and Return are the field's.
    @FocusState private var ownTime: Bool
    @Environment(\.meadow) private var meadow

    /// Whether the date and time field has the keys: as SwiftUI's focus says, or as the window's, which a click in
    /// the field gives it before SwiftUI may have heard.
    private var fieldHasKeys: Bool {
        ownTime || NSApp.keyWindow?.firstResponder is NSDatePicker
    }

    var body: some View {
        let presets = SendTimes.presets()
        ComposePop(title: String(localized: "Send later")) {
            if composer.pendingAfter > 0 {
                Button { pick(0) } label: {
                    MeadowPaletteRow(String(localized: "Send now instead"), icon: .send, pop: true)
                }
                .buttonStyle(.plain)
            }
            ForEach(Array(presets.enumerated()), id: \.offset) { index, preset in
                Button { pick(preset.epoch) } label: {
                    MeadowPaletteRow(preset.label, icon: .clock, keys: ["\(index + 1)"], picked: focused == index,
                                     pop: true)
                }
                .buttonStyle(.plain)
                .focusable()
                .focused($focused, equals: index)
                .focusEffectDisabled()
                // A digit typed in the time field is the time's, never a preset's.
                .keyboardShortcut(ownTime ? nil : KeyboardShortcut(KeyEquivalent(Character("\(index + 1)")), modifiers: []))
            }
            MeadowPopRule()
            HStack(spacing: 8) {
                MeadowIconView(.cal).foregroundStyle(meadow.ink3)
                DatePicker("Pick a date & time", selection: $custom)
                    .datePickerStyle(.compact)
                    .labelsHidden()
                    .focused($ownTime)
                Spacer(minLength: 6)
                Button("Schedule", action: scheduleOwn)
                    .meadowButton(small: true)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 4)
            Text(Self.whileOpen)
                .font(.meadow(.ui, 12))
                .foregroundStyle(meadow.ink3)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 10)
                .padding(.top, 2)
                .padding(.bottom, 6)
        }
        .frame(minWidth: 300)
        .defaultFocus($focused, 0)
        .onAppear { focused = 0 }
        .onKeyPress(.downArrow) { move(1, among: presets.count) }
        .onKeyPress(.upArrow) { move(-1, among: presets.count) }
        .onKeyPress(.return) {
            // Return in the time field schedules the time typed there.
            if fieldHasKeys {
                scheduleOwn()
                return .handled
            }
            guard let at = focused, presets.indices.contains(at) else { return .ignored }
            pick(presets[at].epoch)
            return .handled
        }
        // A preset's digit where the layout's digits aren't ASCII (Arabic's ١), which the shortcuts above never match.
        .onKeyPress(phases: .down) { press in
            guard !fieldHasKeys, press.modifiers.isEmpty, let digit = SingleKey.typed(press)?.wholeNumberValue,
                  presets.indices.contains(digit - 1) else { return .ignored }
            pick(presets[digit - 1].epoch)
            return .handled
        }
    }

    /// The date and time of one's own, once it is still to come.
    private func scheduleOwn() {
        let when = Int(custom.timeIntervalSince1970)
        if Double(when) > Date().timeIntervalSince1970 {
            pick(when)
        } else {
            composer.say("Pick a future time")
        }
    }

    /// Move the keyboard `by` presets, from the first to the last and no further, while it is on one; the time
    /// field's arrows step its time.
    private func move(_ by: Int, among count: Int) -> KeyPress.Result {
        guard !fieldHasKeys, let at = focused else { return .ignored }
        focused = min(max(at + by, 0), count - 1)
        return .handled
    }

    /// MailMind sends only while it is open, so a message for later needs it open then.
    static let whileOpen = String(localized: """
        It goes while MailMind is open: keep it open then, or turn on Open at Login in Settings › General.
        """)

    private func pick(_ when: Int) {
        composer.laterOpen = false
        composer.send(after: when)
    }
}

/// The AI draft's options: its tone, kept as a preference, and what the reply should say.
struct DraftOptions: View {
    let chooseTone: (API.DraftTone) -> Void
    let go: (String) -> Void
    @State private var tone: API.DraftTone
    @State private var say = ""
    @Environment(\.meadow) private var meadow

    init(tone: API.DraftTone, chooseTone: @escaping (API.DraftTone) -> Void, go: @escaping (String) -> Void) {
        _tone = State(initialValue: tone)
        self.chooseTone = chooseTone
        self.go = go
    }

    var body: some View {
        ComposePop(title: String(localized: "Draft a reply")) {
            VStack(alignment: .leading, spacing: 10) {
                Text("Tone").font(.meadow(.ui, 12.5, .bold)).foregroundStyle(meadow.ink2)
                MeadowSegmentedChoice(label: String(localized: "Tone"), selection: $tone,
                                      options: [API.DraftTone.brief, .balanced, .warm, .formal].map { ($0, $0.label) })
                    .onChange(of: tone) { _, chosen in chooseTone(chosen) }
                HStack(spacing: 4) {
                    Text("What should it say?").foregroundStyle(meadow.ink2)
                    Text("optional").foregroundStyle(meadow.ink3)
                }
                .font(.meadow(.ui, 12.5, .bold))
                TextField("e.g. yes, but not before 3 pm", text: $say)
                    .meadowComposeField()
                    .onSubmit { go(say.trimmingCharacters(in: .whitespacesAndNewlines)) }
                HStack {
                    Spacer()
                    Button {
                        go(say.trimmingCharacters(in: .whitespacesAndNewlines))
                    } label: {
                        MeadowIconView(.wind, size: 15)
                        Text("Draft")
                    }
                    .meadowButton(.ai, small: true)
                    .keyboardShortcut(.defaultAction)
                }
            }
            .padding(.horizontal, 10)
            .padding(.bottom, 6)
        }
        .frame(width: 340)
    }
}

/// Snippets: words kept under a name, brought in with ;name and Tab. Kept on the server, as the page keeps them.
struct SnippetsPanel: View {
    let composer: Composer
    let host: any ComposeHost
    let close: () -> Void
    @State private var trigger = ""
    @State private var text: String
    @Environment(\.meadow) private var meadow

    init(composer: Composer, host: any ComposeHost, close: @escaping () -> Void) {
        self.composer = composer
        self.host = host
        self.close = close
        _text = State(initialValue: composer.selectedText)   // what's selected is what's most likely to be kept
    }

    var body: some View {
        ComposePop(title: String(localized: "Snippets")) {
            if host.snippets.isEmpty {
                Text("Words you use often, kept under a name. Type ;name in your message and press Tab.")
                    .font(.meadow(.ui, 13))
                    .foregroundStyle(meadow.ink2)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 10)
                    .padding(.bottom, 4)
            }
            ForEach(host.snippets, id: \.trigger) { snippet in
                HStack(spacing: 2) {
                    Button {
                        close()
                        composer.insertSnippet(snippet)
                    } label: {
                        MeadowPaletteRow(title: ";\(snippet.trigger)", detail: snippet.text, pop: true) { EmptyView() }
                    }
                    .buttonStyle(.plain)
                    .help("Put it in the message")
                    Button {
                        trigger = snippet.trigger
                        text = snippet.text
                    } label: {
                        MeadowIconView(.pen, size: 15)
                    }
                    .meadowButton(.quiet, small: true, icon: true)
                    .accessibilityLabel("Edit ;\(snippet.trigger)")
                    Button {
                        change(API.SnippetChange(trigger: snippet.trigger, verb: .delete))
                    } label: {
                        MeadowIconView(.trash, size: 15)
                    }
                    .meadowButton(.quiet, del: true, small: true, icon: true)
                    .accessibilityLabel("Delete ;\(snippet.trigger)")
                }
            }
            MeadowPopRule()
            VStack(alignment: .leading, spacing: 6) {
                Text("Name").font(.meadow(.ui, 12.5, .bold)).foregroundStyle(meadow.ink2)
                TextField("thanks", text: $trigger).meadowComposeField()
                HStack(spacing: 6) {
                    Text("Text").font(.meadow(.ui, 12.5, .bold)).foregroundStyle(meadow.ink2)
                    Text("Tab goes through each {{placeholder}}").font(.meadow(.ui, 12)).foregroundStyle(meadow.ink3)
                }
                TextEditor(text: $text)
                    .font(.meadow(.ui, 14))
                    .scrollContentBackground(.hidden)
                    .padding(4)
                    .frame(height: 64)
                    .background(meadow.paper, in: RoundedRectangle(cornerRadius: 9))
                    .overlay(RoundedRectangle(cornerRadius: 9).strokeBorder(meadow.line2))
                HStack {
                    Spacer()
                    Button("Save") {
                        change(API.SnippetChange(text: text, trigger: trigger, verb: .save)) {
                            trigger = ""
                            text = ""
                        }
                    }
                    .meadowButton(.primary, small: true)
                    .disabled(trigger.trimmingCharacters(in: .whitespaces).isEmpty
                        || text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
            .padding(.horizontal, 10)
            .padding(.bottom, 6)
        }
        .frame(width: 360)
    }

    private func change(_ change: API.SnippetChange, then done: @escaping () -> Void = {}) {
        Task {
            if let failed = await host.changeSnippets(change) {
                composer.say(failed)
            } else {
                done()
            }
        }
    }
}
#endif
