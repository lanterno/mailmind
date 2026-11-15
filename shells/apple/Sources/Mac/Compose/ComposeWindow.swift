#if os(macOS)
import AppKit
import SwiftUI
import UniformTypeIdentifiers

/// A composer in a window of its own, the Mac's way: several can be open at once, each with its own draft. The
/// window holds what `Composing` opened under its id, and closes once that composer has (sent, discarded, or
/// closed with what it held saved).
struct ComposeWindow: View {
    static let scene = "compose"
    let id: UUID?
    @Environment(\.dismiss) private var dismiss
    private var composing: Composing { .shared }

    var body: some View {
        if let id, let composer = composing.composers[id] {
            ComposerForm(composer: composer, host: composing, aliases: composing.digest?.aliases ?? [:]) { tone in
                Task { await Composing.shared.change(API.ChangedPreferences(draftTone: tone)) }
            }
            .navigationTitle(ComposerForm.title(composer.mode))
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

/// What the composer's window shows, as the page's `compose/Composer.tsx` lays it out: From, the recipients with
/// completion, the subject, the AI's rewrites, the text or its preview, the quote, the files, the send guard's
/// lines, and the foot's buttons.
struct ComposerForm: View {
    let composer: Composer
    let host: any ComposeHost
    let aliases: [String: String?]
    let chooseTone: (API.DraftTone) -> Void

    @FocusState private var subjectFocused: Bool
    @State private var quoteOpen = false
    @State private var snippetsShown = false
    @State private var draftOptions = false
    @State private var translating = false
    @State private var language = "Spanish"
    @State private var dropping = false

    static func title(_ mode: API.ComposeMode) -> String {
        switch mode {
        case .new: "New Message"
        case .reply: "Reply"
        case .replyall: "Reply All"
        case .forward: "Forward"
        }
    }

    private static let rewrites: [API.RewriteOp] = [.shorten, .elaborate, .formal, .friendly, .fix, .translate]

    var body: some View {
        VStack(spacing: 0) {
            heading
                .zIndex(1)   // a completion list hangs over the text below it
            modeBar
            writing
            if let check = composer.check {
                GuardStrip(composer: composer, check: check)
            }
            Divider()
            foot
        }
        .frame(minWidth: 640, idealWidth: 720, minHeight: 460, idealHeight: 640)
        .background(Color(nsColor: .textBackgroundColor))
        .overlay(alignment: .bottom) { noticeView }
        .overlay {
            if dropping {
                RoundedRectangle(cornerRadius: 8).stroke(Color.accentColor, lineWidth: 3).padding(4)
                    .allowsHitTesting(false)
            }
        }
        .onDrop(of: [.fileURL], isTargeted: $dropping) { providers in
            Self.urls(from: providers) { urls in composer.add(urls.map { .file($0) }) }
            return true
        }
        .onChange(of: composer.focusRequest) { _, wanted in place(wanted) }
        .onChange(of: composer.wantsFiles) { _, wanted in
            guard wanted else { return }
            composer.wantsFiles = false
            choose(pictures: false)
        }
        .onAppear { place(composer.focusRequest ?? firstPlace) }
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

    // MARK: The head

    private var heading: some View {
        VStack(alignment: .leading, spacing: 0) {
            if !composer.note.isEmpty {
                Text(composer.note)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.vertical, 8)
                Divider()
            }
            row("From") { fromChoice }
            Divider()
            row("To") {
                ChipField(composer: composer, field: .to, label: "To", routes: host.routes)
                if !composer.showCc {
                    Button("Cc/Bcc") {
                        composer.showCopies()
                        composer.focusRequest = .field(.cc)
                    }
                    .buttonStyle(.link)
                }
            }
            if composer.showCc {
                Divider()
                row("Cc") { ChipField(composer: composer, field: .cc, label: "Cc", routes: host.routes) }
                Divider()
                row("Bcc") { ChipField(composer: composer, field: .bcc, label: "Bcc", routes: host.routes) }
            }
            Divider()
            row("Subject") {
                TextField("Subject", text: Binding(get: { composer.subject }, set: { composer.setSubject($0) }))
                    .textFieldStyle(.plain)
                    .focused($subjectFocused)
            }
            if host.aiOn {
                Divider()
                rewriteRow
            }
            Divider()
        }
        .padding(.horizontal, 16)
    }

    private func row(_ label: String, @ViewBuilder _ content: () -> some View) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(label)
                .foregroundStyle(.secondary)
                .frame(width: 58, alignment: .trailing)
            content()
        }
        .padding(.vertical, 6)
    }

    @ViewBuilder
    private var fromChoice: some View {
        let senders = host.senders
        if senders.count > 1 {
            Picker("From", selection: Binding(get: { composer.from }, set: { composer.setFrom($0) })) {
                ForEach(senders, id: \.self) { address in
                    Text("\(Self.alias(address, aliases)) — \(address)").tag(address)
                }
            }
            .labelsHidden()
            .fixedSize()
            Spacer(minLength: 0)
        } else {
            Text(composer.from)
            Spacer(minLength: 0)
        }
    }

    /// An account's name as the window shows it: its alias, or the part before the @.
    static func alias(_ address: String, _ aliases: [String: String?]) -> String {
        if let named = aliases[address] ?? nil, !named.isEmpty { return named }
        return String(address.split(separator: "@", omittingEmptySubsequences: false).first ?? "")
    }

    private var rewriteRow: some View {
        HStack(spacing: 6) {
            Label(composer.busy == .rewrite ? "Rewriting…" : "Rewrite", systemImage: "wind")
                .foregroundStyle(.secondary)
            ForEach(Self.rewrites, id: \.self) { op in
                Button(op.label) {
                    if op == .translate {
                        translating = true
                    } else {
                        Task { await composer.rewrite(op) }
                    }
                }
                .disabled(composer.busy != .none)
                .popover(isPresented: op == .translate ? $translating : .constant(false), arrowEdge: .bottom) {
                    translateAsk
                }
            }
            if composer.undoRewrite != nil {
                Button("↩ Undo") { composer.undoTheRewrite() }
            }
            Spacer(minLength: 0)
        }
        .buttonStyle(.bordered)
        .controlSize(.small)
        .padding(.vertical, 6)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("AI rewrite (acts on the selection, or the whole message)")
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

    // MARK: The text

    private var modeBar: some View {
        HStack {
            if let offer = composer.offer {
                Text("Tab brings in \(Text(";\(offer.trigger)").bold())")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Picker("Write or preview the message", selection: Binding(get: { composer.previewing }, set: { wanted in
                if wanted { Task { await composer.showPreview() } } else { composer.write() }
            })) {
                Text("Write").tag(false)
                Text("Preview").tag(true)
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .fixedSize()
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 6)
    }

    @ViewBuilder
    private var writing: some View {
        VStack(alignment: .leading, spacing: 8) {
            if composer.previewing {
                if let preview = composer.preview {
                    if !preview.formatted {
                        Text("No Markdown here: this goes out as plain text.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .padding(.horizontal, 16)
                    }
                    QuoteFrame(html: preview.html, title: "How your message will read")
                } else {
                    ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            } else {
                ComposeTextView(composer: composer, text: composer.body, selectionRequest: composer.selectionRequest,
                                focusBody: composer.focusRequest == .body)
                    .overlay(alignment: .topLeading) {
                        if composer.body.isEmpty {
                            Text("Write your message…")
                                .foregroundStyle(.tertiary)
                                .padding(.horizontal, 17)
                                .padding(.vertical, 10)
                                .allowsHitTesting(false)
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
            VStack(alignment: .leading, spacing: 4) {
                Button(Seeds.quoteLabel(mode: composer.mode, fwdAttach: composer.fwdAttach, open: quoteOpen)) {
                    quoteOpen.toggle()
                }
                .buttonStyle(.plain)
                .foregroundStyle(.secondary)
                .accessibilityValue(quoteOpen ? "shown" : "hidden")
                if quoteOpen {
                    if let html = quote.html {
                        QuoteFrame(html: html, title: "The message you’re answering")
                            .frame(height: 240)
                            .clipShape(RoundedRectangle(cornerRadius: 6))
                    } else {
                        ScrollView {
                            Text(quote.text.isEmpty ? "(no preview)" : quote.text)
                                .font(.callout)
                                .textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .frame(maxHeight: 200)
                    }
                }
            }
            .padding(.horizontal, 16)
        }
    }

    @ViewBuilder
    private var files: some View {
        if !composer.attachments.isEmpty || !composer.reading.isEmpty {
            FlowLayout(spacing: 6) {
                ForEach(Array(composer.attachments.enumerated()), id: \.offset) { index, file in
                    HStack(spacing: 4) {
                        Image(systemName: file.cid != nil ? "photo" : "paperclip")
                        Text(file.filename).lineLimit(1)
                        if file.cid != nil { Text("in the text").foregroundStyle(.secondary) }
                        Text(FileSize.words(file.size)).foregroundStyle(.secondary)
                        Button {
                            composer.removeAttachment(at: index)
                        } label: {
                            Image(systemName: "xmark.circle.fill")
                        }
                        .buttonStyle(.plain)
                        .foregroundStyle(.secondary)
                        .accessibilityLabel("Remove \(file.filename)")
                    }
                    .font(.callout)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(.quaternary, in: Capsule())
                }
                ForEach(composer.reading) { reading in
                    HStack(spacing: 4) {
                        ProgressView().controlSize(.mini)
                        Text("Reading \(reading.name)…").foregroundStyle(.secondary)
                    }
                    .font(.callout)
                }
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 8)
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

    private var foot: some View {
        HStack(spacing: 8) {
            Button { choose(pictures: false) } label: { Label("Attach", systemImage: "paperclip") }
                .labelStyle(.iconOnly)
                .help("Attach files")
            Button { choose(pictures: true) } label: { Label("Picture", systemImage: "photo") }
                .labelStyle(.iconOnly)
                .help("Put a picture in the text")
            Button { snippetsShown.toggle() } label: { Text("Snippets").fixedSize() }
                .help("Words you keep, to bring in with ;name and Tab")
                .popover(isPresented: $snippetsShown, arrowEdge: .top) {
                    SnippetsPanel(composer: composer, host: host) { snippetsShown = false }
                }
            if host.aiOn && composer.aiOffered {
                draftButtons
            }
            Text(composer.savedLine)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .layoutPriority(-1)   // the first to give way when the window is narrow
            Spacer(minLength: 8)
            Button { Task { await composer.discard() } } label: { Label("Discard", systemImage: "trash") }
                .labelStyle(.iconOnly)
                .help("Discard this message, and its draft")
            Button { _ = composer.save() } label: { Text("Save Draft").fixedSize() }
                .keyboardShortcut("s")
                .help("Save draft (⌘S)")
            Button { composer.laterOpen.toggle() } label: { Label("Later", systemImage: "clock").fixedSize() }
                .help("Send later")
                .popover(isPresented: Binding(get: { composer.laterOpen }, set: { composer.laterOpen = $0 }),
                         arrowEdge: .top) {
                    LaterChoices(composer: composer)
                }
            Button {
                if composer.check != nil { composer.confirmGuard() } else { composer.send(after: composer.pendingAfter) }
            } label: {
                Label(sendLabel, systemImage: "paperplane.fill").fixedSize()
            }
            .buttonStyle(.borderedProminent)
            .keyboardShortcut(.return, modifiers: .command)
            .help(sendHelp)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
    }

    private var draftButtons: some View {
        let tone = host.tone
        let drafting = composer.busy == .draft
        return HStack(spacing: 2) {
            Button {
                Task { await composer.draftReply() }
            } label: {
                Label(drafting ? "Drafting…" : tone == .balanced ? "Draft" : "Draft · \(tone.label.lowercased())",
                      systemImage: "wind")
                    .fixedSize()
            }
            .help("Draft a reply with AI, \(tone.label.lowercased()) tone")
            Button {
                draftOptions.toggle()
            } label: {
                Image(systemName: "chevron.down")
            }
            .accessibilityLabel("Draft options")
            .popover(isPresented: $draftOptions, arrowEdge: .top) {
                DraftOptions(tone: tone, chooseTone: chooseTone) { instruction in
                    draftOptions = false
                    Task { await composer.draftReply(instruction: instruction) }
                }
            }
        }
        .disabled(drafting)
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

    @ViewBuilder
    private var noticeView: some View {
        if let notice = composer.notice {
            Text(notice)
                .font(.callout)
                .padding(.horizontal, 14)
                .padding(.vertical, 8)
                .background(.regularMaterial, in: Capsule())
                .shadow(radius: 3, y: 1)
                .padding(.bottom, 64)
                .transition(.opacity)
                .allowsHitTesting(false)
        }
    }
}

/// A recipient field: its chips, what is being typed, and the addresses the server suggests for it.
struct ChipField: View {
    let composer: Composer
    let field: ComposeField
    let label: String
    let routes: (any Routes)?
    @State private var items: [API.Suggestion] = []
    @State private var at = -1
    @State private var lookup: Task<Void, Never>?
    @State private var editing = false

    var body: some View {
        let chips = composer.recipients(field)
        ChipFlow(spacing: 4) {
            ForEach(Array(chips.enumerated()), id: \.offset) { index, recipient in
                chip(recipient, at: index)
            }
            RecipientInput(
                label: label,
                placeholder: field == .to && chips.isEmpty ? "name@example.com" : "",
                text: Binding(get: { composer.typed[field] ?? "" }, set: { value in
                    composer.setTyped(field, value)
                    lookUp(value)
                }),
                move: move,
                accept: accept,
                commit: {
                    composer.commitChip(field, composer.typed[field] ?? "")
                    close()
                },
                backspaceWhenEmpty: {
                    if !chips.isEmpty { composer.removeChip(field, at: chips.count - 1) }
                },
                escape: {
                    guard !items.isEmpty else { return false }
                    close()
                    return true
                },
                focused: composer.focusRequest == .field(field),
                focusTaken: {
                    editing = true
                    if composer.focusRequest == .field(field) { composer.focusRequest = nil }
                },
                lostFocus: {
                    editing = false
                    close()
                }
            )
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .overlay(alignment: .bottomLeading) {
            if !items.isEmpty { suggestions }
        }
    }

    private func chip(_ recipient: Recipient, at index: Int) -> some View {
        let address = recipient.address
        let fresh = !recipient.bad && composer.isNew(address)
        return HStack(spacing: 3) {
            Text(recipient.value).lineLimit(1)
            if fresh {
                Text("new")
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(.orange)
                    .accessibilityLabel(", new to you")
            }
            Button {
                composer.removeChip(field, at: index)
            } label: {
                Image(systemName: "xmark")
                    .font(.caption2.weight(.bold))
            }
            .buttonStyle(.plain)
            .foregroundStyle(.secondary)
            .accessibilityLabel("Remove \(recipient.value)\(recipient.bad ? ", not a valid address" : fresh ? ", new to you" : "")")
        }
        .padding(.horizontal, 7)
        .padding(.vertical, 2)
        .background(recipient.bad ? Color.red.opacity(0.15) : Color.accentColor.opacity(0.12), in: Capsule())
        .overlay(Capsule().stroke(recipient.bad ? Color.red.opacity(0.6) : .clear))
        .help(fresh ? "No earlier mail with this address in MailMind" : recipient.bad ? "Not a valid address" : address)
    }

    private var suggestions: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(Array(items.enumerated()), id: \.element.addr) { index, item in
                Button {
                    take(item)
                } label: {
                    HStack {
                        Text(item.name.isEmpty ? item.addr : item.name)
                        Text(item.addr).foregroundStyle(index == at ? .primary : .secondary)
                        Spacer(minLength: 0)
                    }
                    .padding(.horizontal, 10)
                    .padding(.vertical, 5)
                    .contentShape(Rectangle())
                    .background(index == at ? Color.accentColor.opacity(0.25) : .clear)
                }
                .buttonStyle(.plain)
            }
        }
        .frame(minWidth: 260, alignment: .leading)
        .fixedSize()
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 6))
        .overlay(RoundedRectangle(cornerRadius: 6).stroke(.separator))
        .shadow(radius: 6, y: 2)
        .alignmentGuide(.bottom) { $0[.top] - 4 }
        .accessibilityLabel("Addresses for \(label)")
    }

    private func close() {
        lookup?.cancel()
        items = []
        at = -1
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
            let have = Set(composer.recipients(field).map(\.address))
            items = read.items.filter { !have.contains($0.addr.lowercased()) }
            at = -1
        }
    }

    private func move(_ by: Int) -> Bool {
        guard !items.isEmpty else { return false }
        at = (at + by + items.count + (at < 0 && by < 0 ? 1 : 0)) % items.count
        return true
    }

    private func accept() -> Bool {
        guard items.indices.contains(at) else { return false }
        take(items[at])
        return true
    }

    private func take(_ item: API.Suggestion) {
        close()
        composer.commitChip(field, item.addr)
    }
}

/// Chips in lines, with the field after them taking what is left of its line (a line of its own when little is).
struct ChipFlow: Layout {
    var spacing: CGFloat = 4
    var fieldMin: CGFloat = 140

    private func lines(_ width: CGFloat, _ subviews: Subviews) -> [(frame: CGRect, index: Int)] {
        var x: CGFloat = 0, y: CGFloat = 0, line: CGFloat = 0
        var placed: [(CGRect, Int)] = []
        for (index, view) in subviews.enumerated() {
            let last = index == subviews.count - 1
            var size = view.sizeThatFits(.unspecified)
            if last { size.width = fieldMin }
            if x > 0 && x + size.width > width {
                y += line + spacing
                x = 0
                line = 0
            }
            if last { size.width = max(fieldMin, width - x) }
            placed.append((CGRect(x: x, y: y, width: size.width, height: size.height), index))
            x += size.width + spacing
            line = max(line, size.height)
        }
        return placed
    }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? 400
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

/// The send guard's strip: what holds this send, each line's fix, and the way past it when there is one.
struct GuardStrip: View {
    let composer: Composer
    let check: GuardView

    var body: some View {
        let later = check.sendAfter > 0
        VStack(alignment: .leading, spacing: 6) {
            Text(later ? "Not scheduled yet" : "Not sent yet").font(.headline)
            ForEach(check.lines) { line in
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Image(systemName: line.soft ? "info.circle" : "exclamationmark.triangle.fill")
                        .foregroundStyle(line.soft ? Color.secondary : Color.orange)
                    Text(line.text)
                        .foregroundStyle(line.soft ? .secondary : .primary)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 6)
                    if let fix = line.fix {
                        Button(fix.label) { composer.fix(fix, field: line.field) }
                            .controlSize(.small)
                    }
                }
            }
            HStack {
                Text(check.hard ? "Esc keeps editing" : "⌘Return \(later ? "schedules" : "sends") anyway · Esc keeps editing")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                Button("Keep Editing") { composer.dismissGuard() }
                    .keyboardShortcut(.cancelAction)
                if !check.hard {
                    Button(later ? "Schedule Anyway" : "Send Anyway") { composer.confirmGuard() }
                        .disabled(!check.armed)
                }
            }
        }
        .padding(12)
        .background(Color.orange.opacity(0.1), in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.orange.opacity(0.35)))
        .padding(.horizontal, 12)
        .padding(.bottom, 8)
        .accessibilityElement(children: .contain)
        .accessibilityLabel(later ? "Not scheduled yet" : "Not sent yet")
    }
}

/// Send later's choices, made as it opens so "in 1 hour" means an hour from now however long the window has been
/// open.
struct LaterChoices: View {
    let composer: Composer
    @State private var custom = Date().addingTimeInterval(3600)

    var body: some View {
        let presets = SendTimes.presets()
        VStack(alignment: .leading, spacing: 4) {
            if composer.pendingAfter > 0 {
                choice("·", "Send now instead") { pick(0) }
            }
            ForEach(Array(presets.enumerated()), id: \.offset) { index, preset in
                choice("\(index + 1)", preset.label) { pick(preset.epoch) }
                    .keyboardShortcut(KeyEquivalent(Character("\(index + 1)")), modifiers: [])
            }
            Divider().padding(.vertical, 4)
            HStack {
                Text("Custom…")
                DatePicker("Custom…", selection: $custom)
                    .datePickerStyle(.compact)
                    .labelsHidden()
                Button("Schedule") {
                    let when = Int(custom.timeIntervalSince1970)
                    if Double(when) > Date().timeIntervalSince1970 {
                        pick(when)
                    } else {
                        composer.say("Pick a future time")
                    }
                }
            }
        }
        .padding(12)
        .frame(minWidth: 280)
    }

    private func choice(_ key: String, _ label: String, _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 10) {
                Text(key).foregroundStyle(.secondary).monospacedDigit().frame(width: 12)
                Text(label)
                Spacer(minLength: 0)
            }
            .contentShape(Rectangle())
            .padding(.vertical, 3)
        }
        .buttonStyle(.plain)
    }

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

    init(tone: API.DraftTone, chooseTone: @escaping (API.DraftTone) -> Void, go: @escaping (String) -> Void) {
        _tone = State(initialValue: tone)
        self.chooseTone = chooseTone
        self.go = go
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Tone").font(.headline)
            Picker("Tone", selection: $tone) {
                ForEach([API.DraftTone.brief, .balanced, .warm, .formal], id: \.self) { Text($0.label).tag($0) }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .onChange(of: tone) { _, chosen in chooseTone(chosen) }
            HStack(spacing: 4) {
                Text("What should it say?")
                Text("optional").foregroundStyle(.secondary)
            }
            TextField("e.g. yes, but not before 3 pm", text: $say)
                .onSubmit { go(say.trimmingCharacters(in: .whitespacesAndNewlines)) }
            HStack {
                Spacer()
                Button {
                    go(say.trimmingCharacters(in: .whitespacesAndNewlines))
                } label: {
                    Label("Draft", systemImage: "wind")
                }
                .keyboardShortcut(.defaultAction)
            }
        }
        .padding(14)
        .frame(width: 320)
    }
}

/// Snippets: words kept under a name, brought in with ;name and Tab. Kept on the server, as the page keeps them.
struct SnippetsPanel: View {
    let composer: Composer
    let host: any ComposeHost
    let close: () -> Void
    @State private var trigger = ""
    @State private var text: String

    init(composer: Composer, host: any ComposeHost, close: @escaping () -> Void) {
        self.composer = composer
        self.host = host
        self.close = close
        _text = State(initialValue: composer.selectedText)   // what's selected is what's most likely to be kept
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if host.snippets.isEmpty {
                Text("Words you use often, kept under a name. Type ;name in your message and press Tab.")
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            ForEach(host.snippets, id: \.trigger) { snippet in
                HStack {
                    Button {
                        close()
                        composer.insertSnippet(snippet)
                    } label: {
                        VStack(alignment: .leading, spacing: 1) {
                            Text(";\(snippet.trigger)").bold()
                            Text(snippet.text).lineLimit(1).foregroundStyle(.secondary)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .help("Put it in the message")
                    Button("Edit") {
                        trigger = snippet.trigger
                        text = snippet.text
                    }
                    .accessibilityLabel("Edit ;\(snippet.trigger)")
                    Button("Delete") { change(API.SnippetChange(trigger: snippet.trigger, verb: .delete)) }
                        .accessibilityLabel("Delete ;\(snippet.trigger)")
                }
                .controlSize(.small)
            }
            Divider()
            Text("Name").font(.caption).foregroundStyle(.secondary)
            TextField("thanks", text: $trigger)
            HStack(spacing: 4) {
                Text("Text").font(.caption).foregroundStyle(.secondary)
                Text("Tab goes through each {{placeholder}}").font(.caption2).foregroundStyle(.tertiary)
            }
            TextEditor(text: $text)
                .font(.body)
                .frame(height: 64)
                .overlay(RoundedRectangle(cornerRadius: 4).stroke(.separator))
            HStack {
                Spacer()
                Button("Save") {
                    change(API.SnippetChange(text: text, trigger: trigger, verb: .save)) {
                        trigger = ""
                        text = ""
                    }
                }
                .disabled(trigger.trimmingCharacters(in: .whitespaces).isEmpty
                    || text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(14)
        .frame(width: 340)
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
