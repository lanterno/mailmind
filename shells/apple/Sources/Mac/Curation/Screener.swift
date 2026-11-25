#if os(macOS)
import Observation
import SwiftUI

/// The first-contact gate for one account (`/api/screener`): senders this inbox hasn't decided on, a sender tray
/// rather than a message list. The AI may suggest a verdict for the senders it is sure of, on a press, kept for the
/// session so coming back spends nothing. Only a press decides, and it writes a rule (`/api/rules`).
@MainActor
@Observable
final class ScreenerModel {
    /// A sender decided on in this session, for "Decided recently".
    struct Decided: Equatable {
        let sender: API.ScreenerItem
        let verdict: API.Verdict
        let at: Date
    }

    /// What the AI said of a sender at the gate.
    enum Said: Equatable {
        /// Not asked yet: the AI never was, or the sender came to the gate after it was.
        case notAsked
        /// Asked, and not sure enough to say.
        case unsure
        case suggests(API.ScreenerSuggestion)
    }

    let account: String
    private(set) var queue: API.ScreenerQueue?
    /// The AI's verdicts, by sender, once asked for.
    private(set) var suggestions: [String: API.ScreenerSuggestion]?
    /// The senders the AI was last asked about, sure of them or not.
    private(set) var asked: Set<String> = []
    private(set) var suggesting = false
    /// Senders decided on, gone from the tray before the server's answer comes.
    private(set) var decided: Set<String> = []
    /// Senders put off with Decide later, at the back of the queue in the order they were put off.
    private(set) var later: [String] = []
    /// The sender at the gate, chosen from the queue; the first one otherwise.
    var chosen: String?
    /// What was decided here this session, the latest first.
    private(set) var recent: [Decided] = []
    /// Each sender's latest message, once read, for the visitor's card; none where the search found none.
    private(set) var letters: [String: API.SearchHit?] = [:]
    @ObservationIgnored let link: CoreLink
    @ObservationIgnored let notices: DeskNotices
    @ObservationIgnored var changed: () -> Void = {}

    init(account: String, link: CoreLink, notices: DeskNotices) {
        self.account = account
        self.link = link
        self.notices = notices
    }

    /// The senders waiting, those put off last.
    var rows: [API.ScreenerItem] {
        let waiting = (queue?.items ?? []).filter { !decided.contains($0.fromAddr) }
        return waiting.filter { !later.contains($0.fromAddr) }
            + later.compactMap { addr in waiting.first { $0.fromAddr == addr } }
    }

    /// The sender at the gate.
    var visitor: API.ScreenerItem? {
        rows.first { $0.fromAddr == chosen } ?? rows.first
    }

    /// How many senders wait for a decision.
    var waiting: Int { queue?.total ?? 0 }

    func load() async {
        guard let routes = link.routes else { return }
        if let read = try? await routes.get(API.Get.Screener.self, ["account": account]) {
            queue = read
            decided.removeAll()
        }
    }

    /// Read the queue again and the letters with it, as mail changed: a sender whose last Inbox message was filed or
    /// deleted leaves the gate, and the card's sender's latest message is read again and replaces the one shown as it
    /// comes. The other letters are read again when their senders come to the gate.
    func reload() async {
        await load()
        let shown = visitor?.fromAddr
        letters = letters.filter { $0.key == shown }
        if let shown, letters[shown] != nil { await readLetter(shown, again: true) }
    }

    /// Whether the gate's card shows `copies`' message, or one of the letters held does.
    func shows(any copies: Set<MailCopy>) -> Bool {
        letters.values.contains { letter in letter.map { copies.contains(MailCopy(API.FolderRow($0))) } ?? false }
    }

    /// Read a sender's latest message for their card: a local search, which spends nothing. Once read it stands,
    /// unless read `again`.
    func readLetter(_ sender: String, again: Bool = false) async {
        guard let routes = link.routes, again || letters[sender] == nil else { return }
        let read = try? await routes.get(API.Get.Search.self, [
            "q": "from:" + Self.quoted(sender), "scope": API.SearchScope.account.rawValue, "account": account,
            "limit": "1", "offset": "0",
        ])
        switch read {
        case .searchPage(let page)?: letters[sender] = .some(page.items.first)
        case .nothingAsked(let none)?: letters[sender] = .some(none.items.first)
        // Not read: none, the first time; read again, the one shown stands.
        case nil: if letters[sender] == nil { letters[sender] = .some(nil) }
        }
    }

    /// A value as the search grammar quotes one (`labelQuery`'s quoting).
    static func quoted(_ value: String) -> String {
        "\"" + value.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"") + "\""
    }

    /// What the AI said of `sender`, if it was asked about them.
    func said(of sender: String) -> Said {
        if let hint = suggestions?[sender] { return .suggests(hint) }
        return asked.contains(sender) ? .unsure : .notAsked
    }

    /// Ask the AI which senders it is sure of, on a press: again later for those who came since.
    func suggest() async {
        guard let routes = link.routes, !suggesting else { return }
        suggesting = true
        defer { suggesting = false }
        // The server asks about the senders waiting in the queue's order, as many as its answer says.
        let waiting = (queue?.items ?? []).map(\.fromAddr)
        do {
            let answer = try await routes.post(API.Post.ScreenerSuggest.self, API.ScreenerSuggestBody(account: account),
                                               asking: link.asksOverLimit)
            suggestions = Dictionary(answer.suggestions.map { ($0.fromAddr, $0) }, uniquingKeysWith: { first, _ in first })
            asked = Set(waiting.prefix(answer.asked)).union(answer.suggestions.map(\.fromAddr))
            if answer.suggestions.isEmpty { notices.say(String(localized: "The AI isn’t sure of any of these")) }
        } catch {
            notices.say(DeskRefusal.isAIOff(error) ? DeskRefusal.aiOff(error)
                : DeskRefusal.isOverLimit(error) ? String(localized: "Today’s AI limit is reached")
                : String(localized: "Couldn’t get suggestions"))
        }
    }

    /// Let a sender in, or screen them out: said as the buttons say it.
    func decide(_ sender: String, _ verdict: API.Verdict) async {
        guard let routes = link.routes else { return }
        let item = rows.first { $0.fromAddr == sender }
        decided.insert(sender)
        if chosen == sender { chosen = nil }
        do {
            let changed = try await routes.post(API.Post.Rules.self, API.RuleBody(token: sender, verdict: verdict == .keep ? .keep : .block))
            let who = Self.calling(item, sender)
            notices.say(verdict == .keep ? String(localized: "Let in \(who)")
                : String(localized: "Screened out \(who)") + (changed.trashed ? " · trashed" : ""))
            if let item { recent.insert(Decided(sender: item, verdict: verdict, at: AppSeams.clock.now), at: 0) }
            self.changed()
            await load()
        } catch {
            decided.remove(sender)
            notices.say(ReadOnlyWords.said(error, else: String(localized: "Couldn’t apply")))
        }
    }

    /// Put a sender off: to the back of the queue, the next one at the gate.
    func putOff(_ sender: String) {
        later.removeAll { $0 == sender }
        later.append(sender)
        if chosen == sender { chosen = nil }
    }

    /// A sender as the rest of the app names them (`Words.calling`): a person by their first name, an organisation
    /// whole, else their address, never its part before the @ alone.
    static func calling(_ item: API.ScreenerItem?, _ sender: String) -> String {
        Words.calling(item?.fromName, address: sender) ?? sender
    }

    /// The page's word for each verdict, as a hint names it.
    static func word(_ verdict: API.Verdict) -> String {
        switch verdict {
        case .keep: String(localized: "Let in")
        case .block: String(localized: "Screen out")
        }
    }
}

/// The Screener as Meadow's gate (`renderGate()`), across the list's and the pane's room: the gate in the meadow with
/// "At the gate" on its sky, what the gate is for, the sender at the gate on a stack of cards (who, their latest
/// message, who the AI thinks they are, and Let in, Screen out or Decide later), and beside it who else waits, who was
/// decided on, and how the gate works. In a narrow room, one column.
struct ScreenerScreen: View {
    let model: ScreenerModel
    /// The account's name, where there is more than one account.
    var name: String?
    /// The accounts in their order, for an account's colour.
    var accounts: [String] = []
    /// Whether the AI's suggestions are offered: not once AI is turned off.
    var suggests = true
    /// Opens a sender's message in the reader.
    var open: (MailCopy) -> Void = { _ in }
    @State private var width: CGFloat = 1000
    @FocusState private var focus: GateFocus?
    @Environment(\.meadow) private var meadow

    private var narrow: Bool { width <= 760 }
    private var wide: Bool { width >= 980 }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                header
                VStack(alignment: .leading, spacing: 0) {
                    Text("""
                        People writing to you for the first time wait here. Let them in once and they go straight \
                        to your inbox; screen them out and their mail stays out of it.
                        """)
                        .font(.meadow(.ui, 15))
                        .foregroundStyle(meadow.ink2)
                        .lineSpacing(MeadowFace.ui.lineSpacing(size: 15, lineHeight: 1.55))
                        .frame(maxWidth: 620, alignment: .leading)
                        .padding(.bottom, 20)
                    if wide {
                        HStack(alignment: .top, spacing: 28) {
                            visitor.frame(maxWidth: .infinity, alignment: .leading)
                            side.frame(width: 320)
                        }
                    } else {
                        VStack(alignment: .leading, spacing: 24) {
                            visitor
                            side
                        }
                    }
                }
                .padding(.horizontal, narrow ? 16 : 32)
                .padding(.top, 24)
                .padding(.bottom, 48)
                .frame(maxWidth: 1360, alignment: .leading)
                .frame(maxWidth: .infinity)
            }
        }
        .scrollContentBackground(.hidden)
        // What holds the keys where no button can, behind the gate rather than around it: a button reads the focus
        // of the nearest view that can take it, so around them it drew every button's ring at once.
        .background {
            Color.clear
                .focusable()
                .focused($focus, equals: .screen)
                .focusEffectDisabled()
        }
        .onAppear { holdKeys() }
        // Another account's gate shows in the same place, gone to from the rail, which the click left the keyboard
        // with: the gate takes it as it does as it first shows.
        .onChange(of: model.account) { holdKeys() }
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { width = $0 }
        .task(id: model.account) { await model.load() }
        .task(id: model.visitor?.fromAddr) {
            if let sender = model.visitor?.fromAddr { await model.readLetter(sender) }
        }
        .onKeyPress(phases: .down) { press in
            guard press.modifiers.isEmpty || press.modifiers == .shift, let key = SingleKey.typed(press),
                  "yYnN".contains(key), let sender = model.visitor?.fromAddr else {
                return .ignored
            }
            let verdict: API.Verdict = key.lowercased() == "y" ? .keep : .block
            Task { await model.decide(sender, verdict) }
            return .handled
        }
    }

    /// Put the keyboard on Let in where a button can take it, else on the screen itself: with keyboard navigation off
    /// in System Settings a button takes no focus, and Y and N reach the gate only through a view in it that has it.
    private func holdKeys() {
        focus = NSApp.isFullKeyboardAccessEnabled && model.visitor != nil ? .letIn : .screen
    }

    // MARK: The header

    /// The gate in the meadow, 210 high, with who waits and "At the gate" on its sky (`.gate-art`). The place has one
    /// name, the rail's, so the line over it says who stands there, not another name for it.
    private var header: some View {
        ZStack(alignment: .topLeading) {
            MeadowScene(seed: 5, gate: true, alignment: .bottom)
            VStack(alignment: .leading, spacing: 0) {
                Text((name.map { String(localized: "New senders · \($0)") } ?? String(localized: "New senders")).uppercased())
                    .font(.meadow(.ui, 12, .heavy))
                    .tracking(12 * 0.14)
                    .foregroundStyle(meadow.ink2)
                Text("At the gate")
                    .font(.meadow(.display, narrow ? 30 : 40, .bold))
                    .foregroundStyle(meadow.ink)
                    .padding(.top, 8)
                    .accessibilityAddTraits(.isHeader)
            }
            .padding(.leading, narrow ? 20 : 40)
            .padding(.top, narrow ? 22 : 32)
        }
        .frame(height: narrow ? 170 : 210)
        .frame(maxWidth: .infinity)
        .clipShape(UnevenRoundedRectangle(topLeadingRadius: narrow ? 0 : 16, topTrailingRadius: narrow ? 0 : 16))
    }

    // MARK: The visitor

    @ViewBuilder private var visitor: some View {
        if model.queue == nil {
            Text("Loading…")
                .font(.meadow(.ui, 14))
                .foregroundStyle(meadow.ink3)
                .padding(.vertical, 30)
        } else if let sender = model.visitor {
            GateVisitor(sender: sender, model: model, letter: model.letters[sender.fromAddr] ?? nil,
                        account: meadow.account(of: model.account, among: accounts), accountName: name,
                        suggests: suggests, focus: $focus, open: open)
                .id(sender.fromAddr)
                .onAppear { holdKeys() }
        } else {
            VStack(spacing: 6) {
                Text("No one at the gate.")
                    .font(.meadow(.display, 18, .bold))
                    .foregroundStyle(meadow.ink)
                Text("New people who write to you will wait here until you decide.")
                    .font(.meadow(.ui, 14))
                    .foregroundStyle(meadow.ink2)
            }
            .multilineTextAlignment(.center)
            .frame(maxWidth: .infinity)
            .padding(.horizontal, 18)
            .padding(.top, 30)
            .padding(.bottom, 28)
        }
    }

    // MARK: Beside the visitor

    private var side: some View {
        VStack(alignment: .leading, spacing: 14) {
            let rows = model.rows
            if !rows.isEmpty {
                DeskSideCard(title: String(localized: "Waiting · \(rows.count)"), icon: .gate) {
                    VStack(spacing: 2) {
                        ForEach(rows, id: \.fromAddr) { sender in
                            GateQueueRow(sender: sender, current: sender.fromAddr == model.visitor?.fromAddr,
                                         hint: suggests ? model.suggestions?[sender.fromAddr] : nil,
                                         account: meadow.account(of: model.account, among: accounts)) {
                                model.chosen = sender.fromAddr
                                holdKeys()
                            }
                        }
                    }
                }
            }
            if !model.recent.isEmpty {
                DeskSideCard(title: String(localized: "Decided recently"), icon: .check) {
                    VStack(spacing: 0) {
                        ForEach(Array(model.recent.prefix(5).enumerated()), id: \.offset) { _, done in
                            GateDecided(done: done)
                        }
                    }
                }
            }
            DeskSideCard(title: String(localized: "How the gate works"), icon: .shield) {
                VStack(alignment: .leading, spacing: 6) {
                    bullet("Each choice becomes a rule for that sender.")
                    bullet("Let someone in and their mail goes to your inbox from then on.")
                    bullet("""
                        Screen someone out and their mail stays out of your inbox until you change the rule in \
                        Curate › Rules.
                        """)
                }
            }
        }
    }

    private func bullet(_ words: LocalizedStringKey) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(verbatim: "•")
            Text(words).fixedSize(horizontal: false, vertical: true)
        }
        .font(.meadow(.ui, 13))
        .foregroundStyle(meadow.ink2)
        .lineSpacing(MeadowFace.ui.lineSpacing(size: 13, lineHeight: 1.5))
    }
}

/// Where the Screener holds the keyboard: its Let in button, or the screen around it.
private enum GateFocus: Hashable {
    case screen
    case letIn
}

/// The sender at the gate, on its stack (`.stack`, `.visitor`): who, their latest message, and what to do.
private struct GateVisitor: View {
    let sender: API.ScreenerItem
    let model: ScreenerModel
    /// Their latest message, once read.
    let letter: API.SearchHit?
    let account: Color
    let accountName: String?
    /// Whether who the AI thinks this is, and the way to ask it, are offered.
    let suggests: Bool
    var focus: FocusState<GateFocus?>.Binding
    let open: (MailCopy) -> Void
    @State private var width: CGFloat = 800
    @Environment(\.meadow) private var meadow

    private var who: String { sender.fromName.isEmpty ? sender.fromAddr : sender.fromName }

    var body: some View {
        let wide = width >= 720
        ZStack(alignment: .bottom) {
            // The cards under it, as the mockup's ::before and ::after.
            under(inset: 48).opacity(0.7)
            under(inset: 24).padding(.bottom, 10)
            Group {
                if wide {
                    HStack(alignment: .top, spacing: 30) {
                        message.frame(maxWidth: .infinity, alignment: .leading)
                        Rectangle().fill(meadow.line).frame(width: 1)
                        decision.frame(width: 280)
                    }
                    .fixedSize(horizontal: false, vertical: true)
                } else {
                    VStack(alignment: .leading, spacing: 22) {
                        message
                        decision
                    }
                }
            }
            .padding(.horizontal, 26)
            .padding(.vertical, 24)
            .frame(maxWidth: .infinity, alignment: .leading)
            .meadowPanel(radius: 18, raised: true)
            .padding(.bottom, 22)
            .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { width = $0 }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel(Text("Visitor \(who)"))
    }

    private func under(inset: CGFloat) -> some View {
        UnevenRoundedRectangle(bottomLeadingRadius: 18, bottomTrailingRadius: 18)
            .fill(meadow.paperRaised)
            .frame(height: 30)
            .meadowShadow(meadow.shadow, radius: 18)
            .padding(.horizontal, inset)
    }

    // MARK: Who and what

    private var message: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 14) {
                MeadowAvatar(name: who, size: .huge, account: account)
                VStack(alignment: .leading, spacing: 2) {
                    Text(who).font(.meadow(.ui, 17, .bold)).foregroundStyle(meadow.ink).lineLimit(1)
                    Text(sender.fromAddr).font(.meadow(.ui, 13.5)).foregroundStyle(meadow.ink3).lineLimit(1)
                    HStack(spacing: 6) {
                        if let accountName { MeadowChip(accountName, tone: .account(account)) }
                        MeadowChip(sender.count == 1 ? String(localized: "First message · \(sender.when)")
                                   : String(localized: "\(sender.count) messages · latest \(sender.when)"))
                    }
                    .padding(.top, 4)
                }
            }
            if let letter {
                Text(letter.subject.isEmpty ? String(localized: "(no subject)") : letter.subject)
                    .font(.meadow(.display, 22, .semibold))
                    .foregroundStyle(meadow.ink)
                    .lineSpacing(MeadowFace.display.lineSpacing(size: 22, lineHeight: 1.3, weight: .semibold))
                    .padding(.top, 18)
                if !letter.snippet.isEmpty {
                    Text(letter.snippet)
                        .font(.meadow(.read, 17))
                        .foregroundStyle(meadow.ink)
                        .lineSpacing(MeadowFace.read.lineSpacing(size: 17, lineHeight: 1.68))
                        .frame(maxWidth: 640, alignment: .leading)
                        .fixedSize(horizontal: false, vertical: true)
                        .textSelection(.enabled)
                        .padding(.top, 12)
                }
            }
            trust
                .padding(.top, 14)
        }
    }

    /// Where they write from, and the way to their whole message (`.trust`).
    private var trust: some View {
        HStack(spacing: 8) {
            MeadowIconView(.shield, size: 15)
            Group {
                let domain = Text(sender.domain).font(.meadow(.ui, 12.5, .bold)).foregroundStyle(meadow.ink2)
                if sender.count == 1 {
                    Text("First message from \(domain)")
                } else {
                    Text("\(sender.count) messages from \(domain)")
                }
            }
            .lineLimit(1)
            Spacer(minLength: 8)
            if let letter {
                Button("Read it whole") {
                    open(.named(key: letter.key, folder: letter.folder, uid: letter.uid, account: letter.account))
                }
                .deskLink()
            }
        }
        .font(.meadow(.ui, 12.5))
        .foregroundStyle(meadow.ink3)
        .padding(.horizontal, 12)
        .padding(.vertical, 7)
        .background(RoundedRectangle(cornerRadius: 10).fill(meadow.paper2))
    }

    // MARK: The decision

    private var decision: some View {
        VStack(alignment: .leading, spacing: 14) {
            if suggests { note }
            VStack(spacing: 8) {
                Button { Task { await model.decide(sender.fromAddr, .keep) } } label: {
                    decideLabel(.check, "Let in", key: "Y")
                }
                .buttonStyle(GateButtonStyle(kind: .primary))
                .focusEffectDisabled()
                .focused(focus, equals: .letIn)
                Button { Task { await model.decide(sender.fromAddr, .block) } } label: {
                    decideLabel(.ban, "Screen out", key: "N")
                }
                .buttonStyle(GateButtonStyle(kind: .standard))
                .focusEffectDisabled()
                Button { model.putOff(sender.fromAddr) } label: {
                    decideLabel(.clock, "Decide later", key: nil)
                }
                .buttonStyle(GateButtonStyle(kind: .quiet))
                .focusEffectDisabled()
            }
            Text("Your choice becomes a rule for \(sender.fromAddr). Change it any time in Curate › Rules.")
                .font(.meadow(.ui, 12.5))
                .foregroundStyle(meadow.ink3)
                .lineSpacing(MeadowFace.ui.lineSpacing(size: 12.5, lineHeight: 1.5))
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// Suggest, or Suggest again: the AI asked about every sender waiting now, and at today's limit asked first.
    private func suggestButton(again: Bool) -> some View {
        Button { Task { await model.suggest() } } label: {
            MeadowIconView(.wind, size: 15)
            Text(model.suggesting ? "Reading…" : again ? "Suggest again" : "Suggest")
        }
        .meadowButton(.ai, small: true)
        .disabled(model.suggesting)
        .help("Ask the AI about the senders waiting now (runs the AI)")
    }

    private func decideLabel(_ icon: MeadowIcon, _ words: LocalizedStringKey, key: String?) -> some View {
        HStack(spacing: 7) {
            MeadowIconView(icon)
            Text(words)
            Spacer(minLength: 0)
            if let key { MeadowKey(key) }
        }
    }

    /// Who the AI thinks this is (`.note`), once asked; before that, the way to ask, and once asked, the way to ask
    /// again about everyone waiting now.
    @ViewBuilder private var note: some View {
        VStack(alignment: .leading, spacing: 8) {
            MeadowAIMark("Who is this?")
            switch model.said(of: sender.fromAddr) {
            case .suggests(let hint):
                Text("\(Text(ScreenerModel.word(hint.verdict) + "?").font(.meadow(.ui, 15, .heavy))) \(hint.why)")
                    .font(.meadow(.ui, 15, .semibold))
                    .foregroundStyle(meadow.moss)
                    .lineSpacing(MeadowFace.ui.lineSpacing(size: 15, lineHeight: 1.6, weight: .semibold))
                    .fixedSize(horizontal: false, vertical: true)
            case .unsure:
                Text("The AI isn’t sure about this one. Their message is the best guide.")
                    .font(.meadow(.ui, 14, .semibold))
                    .foregroundStyle(meadow.moss)
                    .fixedSize(horizontal: false, vertical: true)
            case .notAsked where model.suggestions != nil:
                Text("Not asked yet: they came to the gate after the AI was asked. Suggest again to ask about them.")
                    .font(.meadow(.ui, 13.5))
                    .foregroundStyle(meadow.ink2)
                    .fixedSize(horizontal: false, vertical: true)
            case .notAsked:
                Text("The AI can suggest a verdict for the senders it’s sure of. You still decide.")
                    .font(.meadow(.ui, 13.5))
                    .foregroundStyle(meadow.ink2)
                    .fixedSize(horizontal: false, vertical: true)
            }
            suggestButton(again: model.suggestions != nil)
        }
        .padding(.horizontal, 16)
        .padding(.top, 14)
        .padding(.bottom, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background {
            ZStack {
                meadow.mossWash
                MeadowGrain()
            }
            .clipShape(RoundedRectangle(cornerRadius: 14))
        }
    }
}

/// The gate's three buttons (`.decide .btn`): 44 high, their words at the start and their key at the end.
private struct GateButtonStyle: ButtonStyle {
    let kind: MeadowButtonStyle.Kind

    func makeBody(configuration: Configuration) -> some View {
        GateButtonBody(kind: kind, label: configuration.label, pressed: configuration.isPressed)
    }
}

private struct GateButtonBody: View {
    let kind: MeadowButtonStyle.Kind
    let label: ButtonStyleConfiguration.Label
    let pressed: Bool
    @Environment(\.meadow) private var meadow
    @Environment(\.isFocused) private var focused
    @State private var hovering = false

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: 10)
        HStack(spacing: 7) { label }
            .font(.meadow(.ui, 14.5, .bold))
            .lineLimit(1)
            .foregroundStyle(ink)
            .environment(\.meadowOnPrimary, kind == .primary)
            .padding(.horizontal, 17)
            .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
            .background {
                shape.fill(fill)
                    .overlay { shape.strokeBorder(border, lineWidth: 1) }
                    .background {
                        if kind == .primary {
                            MeadowShadowsOutside(shadows: [.init(x: 0, y: 8, blur: 18, spread: -10,
                                                                 color: meadow.primary, inset: false)], radius: 10)
                        }
                    }
            }
            .contentShape(shape)
            .offset(y: pressed ? 1 : 0)
            .meadowFocusRing(focused, radius: 10)
            .onHover { hovering = $0 }
    }

    private var fill: Color {
        switch kind {
        case .primary: hovering ? meadow.primaryHi : meadow.primary
        case .quiet: hovering ? meadow.paper3 : .clear
        default: hovering ? meadow.paper3 : meadow.paperRaised
        }
    }

    private var border: Color {
        switch kind {
        case .primary, .quiet: .clear
        default: meadow.line2
        }
    }

    private var ink: Color {
        switch kind {
        case .primary: meadow.onPrimary
        case .quiet: hovering ? meadow.ink : meadow.ink2
        default: meadow.ink
        }
    }
}

/// Someone else waiting (`.gq`): who, their address, and when they wrote; the one at the gate in the indigo wash.
private struct GateQueueRow: View {
    let sender: API.ScreenerItem
    let current: Bool
    let hint: API.ScreenerSuggestion?
    let account: Color
    let choose: () -> Void
    @Environment(\.meadow) private var meadow
    @FocusState private var focused: Bool
    @State private var hovering = false

    var body: some View {
        Button(action: choose) {
            HStack(spacing: 10) {
                MeadowAvatar(name: sender.fromName.isEmpty ? sender.fromAddr : sender.fromName, account: account,
                             chosen: current)
                VStack(alignment: .leading, spacing: 1) {
                    Text(sender.fromName.isEmpty ? sender.fromAddr : sender.fromName)
                        .font(.meadow(.ui, 13.5, .bold))
                        .foregroundStyle(meadow.ink)
                    Text(hint.map { "\(ScreenerModel.word($0.verdict))? \($0.why)" } ?? sender.fromAddr)
                        .font(.meadow(.ui, 12.5))
                        .foregroundStyle(hint == nil ? meadow.ink3 : meadow.moss)
                }
                .lineLimit(1)
                .frame(maxWidth: .infinity, alignment: .leading)
                Text(sender.when)
                    .font(.meadow(.ui, 12))
                    .foregroundStyle(meadow.ink3)
                    .lineLimit(1)
            }
            .padding(8)
            .background(RoundedRectangle(cornerRadius: 10)
                .fill(current ? meadow.primaryWash : hovering ? meadow.paper2 : .clear))
            .contentShape(RoundedRectangle(cornerRadius: 10))
        }
        .buttonStyle(.plain)
        .focused($focused)
        .focusEffectDisabled()
        .meadowFocusRing(focused, radius: 10)
        .onHover { hovering = $0 }
        .accessibilityAddTraits(current ? .isSelected : [])
    }
}

/// Someone decided on this session (`.gdone`): who, what was decided and where they write from, and when.
private struct GateDecided: View {
    let done: ScreenerModel.Decided
    @Environment(\.meadow) private var meadow

    var body: some View {
        let who = done.sender.fromName.isEmpty ? done.sender.fromAddr : done.sender.fromName
        HStack(spacing: 10) {
            MeadowAvatar(name: who, size: .small)
            VStack(alignment: .leading, spacing: 1) {
                Text(who).font(.meadow(.ui, 13, .bold)).foregroundStyle(meadow.ink)
                Text(verbatim: (done.verdict == .keep ? String(localized: "Let in") : String(localized: "Screened out"))
                     + " · " + done.sender.domain)
                    .font(.meadow(.ui, 12))
                    .foregroundStyle(meadow.ink3)
            }
            .lineLimit(1)
            .frame(maxWidth: .infinity, alignment: .leading)
            Text(EventWhen.clock(done.at))
                .font(.meadow(.ui, 12))
                .foregroundStyle(meadow.ink3)
        }
        .padding(.horizontal, 2)
        .padding(.vertical, 6)
    }
}
#endif
