#if os(macOS)
import AppKit
import SwiftUI

/// What the AI pane offers (#49), worked out from what the server holds and what a provider listed: plain functions,
/// so the tests hold them. The server keeps the choices, its defaults and what it refuses; this is only what each line
/// shows and sends.
enum AIChoices {
    /// The models a provider listed when asked, or the words it was refused in, kept with the provider asked.
    struct Listed: Equatable {
        let provider: API.AiProvider
        var list: API.ModelList?
        var problem: String?
    }

    /// A line of the model menu: `id` "" is the provider's own default.
    struct Option: Equatable {
        let id: String
        let title: String
    }

    /// The AI pane's parts, in their order.
    enum Part: Hashable {
        /// AI mode's switch.
        case mode
        /// The provider.
        case connection
        /// The model servers found on this Mac, and their models, for a model on this Mac.
        case found
        /// The provider's key.
        case key
        /// The model, and the service's URL where the provider asks for one.
        case model
        /// How AI stands, and what goes where.
        case standing
        case spending
        case writtenAhead
    }

    /// What the pane shows: with AI mode off, only its switch, as nothing is sent then; on, the connection by provider
    /// and how it stands, then what AI may spend and write ahead. A model on this Mac starts from what was found
    /// here, and its key, which only some servers ask for, comes after its model.
    static func parts(aiMode: Bool, provider: API.AiProvider) -> [Part] {
        guard aiMode else { return [.mode] }
        let connection: [Part] = provider == .local ? [.connection, .found, .model, .key] : [.connection, .key, .model]
        return [.mode] + connection + [.standing, .spending, .writtenAhead]
    }

    /// What looking for model servers on this Mac came to.
    enum Look: Equatable {
        case looking
        /// The servers that answered, in the core's order; none is an empty list.
        case found([API.LocalServer])
        /// The asking itself failed, in the words to show.
        case failed(String)
    }

    /// What asking for the model servers on this Mac came to: what was found, or the words the asking failed in.
    @MainActor static func look(_ ask: () async throws -> API.LocalServers) async -> Look {
        do {
            return .found(try await ask().servers)
        } catch {
            return .failed((error as? Problem)?.detail ?? String(localized: "Couldn’t look for model servers."))
        }
    }

    /// What the finder's card says where it lists no server: while it looks, when none answered, or why it couldn't
    /// look. Nil where it lists what it found.
    static func lookLine(_ look: Look) -> String? {
        switch look {
        case .looking: String(localized: "Looking for model servers on this Mac…")
        case .found(let servers) where servers.isEmpty: String(localized: "No model server is running on this Mac yet.")
        case .found: nil
        case .failed(let why): why
        }
    }

    /// Where Ollama is got, for a person with no model server yet.
    static let getOllama = URL(string: "https://ollama.com")!

    /// The context a model on this Mac needs for long conversations, which only its server's settings give it.
    static let contextStep = String(localized: "In Ollama’s settings, set Context length to 16K or more.")

    /// For a person with no model server yet, in steps (the page's): get Ollama, the one model suggested to start
    /// with, by the name Ollama lists it under, its context, and look again.
    static let firstSteps = [
        String(localized: "Get Ollama and open it."),
        String(localized: """
            In Ollama, download a model. A good first one is qwen3:8b, as Ollama lists it: it runs well on a Mac with \
            16 GB of memory.
            """),
        contextStep,
        String(localized: "Then look again."),
    ]

    /// What choosing a model a server on this Mac listed sends, in one change: the server's URL ("" for Ollama's,
    /// which is the provider's own) and the model.
    static func choose(_ model: String, on server: API.LocalServer) -> API.ChangedPreferences {
        API.ChangedPreferences(aiBaseUrl: url(typed: server.url, provider: .local), aiModel: model)
    }

    /// Whether a found model is the one chosen: a model on this Mac by that name, at its server's URL.
    static func isChosen(_ model: String, on server: API.LocalServer, preferences: API.Preferences?) -> Bool {
        guard let preferences, preferences.aiProvider == .local, preferences.aiModel == model else { return false }
        func plain(_ url: String) -> String {
            let shown = url.isEmpty ? API.AiProvider.localURL : url.trimmingCharacters(in: .whitespacesAndNewlines)
            return shown.hasSuffix("/") ? String(shown.dropLast()) : shown
        }
        return plain(server.url) == plain(preferences.aiBaseUrl)
    }

    /// What choosing a provider sends: the provider alone, so its model and URL start again from its own.
    static func choose(_ provider: API.AiProvider) -> API.ChangedPreferences {
        API.ChangedPreferences(aiProvider: provider)
    }

    /// The provider's default model by name, where it is known: what its list said, else, while AI is on with the
    /// provider's own model, the one the digest says is in force. Nil where it has none, or none is known yet.
    static func defaultModel(_ provider: API.AiProvider, listed: Listed?, preferences: API.Preferences?,
                             ai: API.AiStatus?) -> String?
    {
        if let listed, listed.provider == provider, let list = listed.list {
            return list.default.flatMap { $0.isEmpty ? nil : $0 }
        }
        guard !provider.needsModel, let ai, ai.isOn, ai.provider == provider, preferences?.aiModel == "",
              !ai.model.isEmpty else { return nil }
        return ai.model
    }

    /// "Default (gpt-6.1-sol)", or "Default" while its name isn't known.
    static func defaultTitle(_ model: String?) -> String {
        model.map { String(localized: "Default (\($0))") } ?? String(localized: "Default")
    }

    /// The model menu's lines: the provider's default first where it has one, then a model chosen by hand that the
    /// list doesn't name, then what the provider listed, its default not again.
    static func options(_ provider: API.AiProvider, chosen: String, defaultModel: String?, listed: Listed?) -> [Option] {
        var options: [Option] = []
        if !provider.needsModel { options.append(Option(id: "", title: defaultTitle(defaultModel))) }
        let models = (listed?.provider == provider ? listed?.list?.models : nil) ?? []
        let others = models.filter { $0 != defaultModel }
        if !chosen.isEmpty, !others.contains(chosen) { options.append(Option(id: chosen, title: chosen)) }
        return options + others.map { Option(id: $0, title: $0) }
    }

    /// The model menu: the default and a model chosen by hand as lines of their own, then what the provider listed,
    /// scrolling where it lists many, the keys going over every one and a pick closing the menu; that the provider is
    /// being asked, while it is (`asking`); then typing one by hand.
    @MainActor static func modelMenu(_ provider: API.AiProvider, chosen: String, defaultModel: String?, listed: Listed?,
                                     asking: Bool, choose: @escaping @MainActor (String) -> Void,
                                     type: @escaping @MainActor () -> Void) -> [MeadowMenu.Part]
    {
        let options = Self.options(provider, chosen: chosen, defaultModel: defaultModel, listed: listed)
        let listedModels = Set((listed?.provider == provider ? listed?.list?.models : nil) ?? [])
        let many = options.count <= 10 ? [] : options.filter { !$0.id.isEmpty && listedModels.contains($0.id) }
        func item(_ option: Option) -> MeadowMenuItem {
            MeadowMenuItem(id: "model-\(option.id)", title: option.title, check: option.id == chosen) { choose(option.id) }
        }
        var parts: [MeadowMenu.Part] = [.title(String(localized: "Model"))]
        parts += options.filter { !many.contains($0) }.map { MeadowMenu.Part.item(item($0)) }
        if !many.isEmpty { parts.append(.scroll(many.map(item), maxHeight: 280)) }
        if asking {
            parts.append(.item(MeadowMenuItem(id: "listing", title: String(localized: "Asking \(provider.name)…"),
                                              disabled: true) {}))
        }
        parts.append(.rule)
        parts.append(.item(MeadowMenuItem(id: "type", title: String(localized: "Type a Model…"), icon: .pen, run: type)))
        return parts
    }

    /// The asks for a provider's models, the latest standing: an answer to an ask made before another is let go, so a
    /// provider chosen while another's list is on its way is asked at once, and its list is the one kept.
    struct Asking: Equatable {
        /// Counts the asks, so an answer knows whether another was made after it.
        private(set) var turn = 0
        /// The provider the latest ask went to, while its answer is on its way.
        private(set) var provider: API.AiProvider?

        /// A new ask, to `provider`: its turn, which its answer brings back to `end`.
        mutating func begin(_ provider: API.AiProvider) -> Int {
            turn += 1
            self.provider = provider
            return turn
        }

        /// No ask after all, a refusal being foreseen: what one made before brings is let go too.
        mutating func drop() {
            turn += 1
            provider = nil
        }

        /// The answer to the ask of `turn` came: whether it stands, no other having been made since.
        mutating func end(_ turn: Int) -> Bool {
            guard turn == self.turn else { return false }
            provider = nil
            return true
        }
    }

    /// What the model menu's button says: the model chosen, else the default, else that one is wanted.
    static func title(_ provider: API.AiProvider, chosen: String, defaultModel: String?) -> String {
        if !chosen.isEmpty { return chosen }
        return provider.needsModel ? String(localized: "Choose a model") : defaultTitle(defaultModel)
    }

    /// What the model line says under it. A model on this Mac is told what a long thread needs, which only its
    /// server's settings give it.
    static func modelHelp(_ provider: API.AiProvider) -> String {
        switch provider {
        case .local: contextStep
        case .compatible: String(localized: "The service has no model of its own: choose one it lists, or type its name.")
        default: String(localized: "The provider’s own, unless you choose another.")
        }
    }

    /// Whether the provider's models can be asked for with no refusal foreseen: a model server's on this Mac always,
    /// a compatible service's once it has a URL, Claude's with a key kept or the environment's, any other's with its
    /// key kept.
    static func canList(_ provider: API.AiProvider, kept: Set<API.AiProvider>?, preferences: API.Preferences?,
                        sources: SecretSources) -> Bool
    {
        switch provider {
        case .local: true
        case .compatible: !(preferences?.aiBaseUrl ?? "").isEmpty
        case .anthropic: kept?.contains(.anthropic) == true || sources.apiKeyVariable
        case .openai, .gemini: kept?.contains(provider) == true
        }
    }

    /// The URL the field shows: the one chosen, else the provider's own (Ollama's, for a model on this Mac).
    static func shownURL(_ provider: API.AiProvider, preferences: API.Preferences?) -> String {
        let chosen = preferences?.aiBaseUrl ?? ""
        return chosen.isEmpty ? provider.urlPrefill : chosen
    }

    /// The URL kept for what was typed: "" for the provider's own, so it goes on following the provider's default.
    static func url(typed: String, provider: API.AiProvider) -> String {
        let url = typed.trimmingCharacters(in: .whitespacesAndNewlines)
        return url == provider.urlPrefill ? "" : url
    }

    /// What the URL line says under it.
    static func urlHelp(_ provider: API.AiProvider) -> String {
        provider == .local
            ? String(localized: "Where your model server listens, on this Mac: 127.0.0.1, ::1 or localhost.")
            : String(localized: "https, or http on this Mac: the address the service’s API answers at.")
    }

    /// The key's name, as its card and its field say it.
    static func keyLabel(_ provider: API.AiProvider) -> String {
        provider.needsKey ? String(localized: "\(provider.name) API key") : String(localized: "API key")
    }

    static func keyPrompt(_ provider: API.AiProvider) -> String {
        if provider == .local { return String(localized: "paste your server’s key") }
        guard let place = provider.keyPlace else { return String(localized: "paste the service’s key, if it takes one") }
        return String(localized: "paste a key from \(place)")
    }

    /// What taking the key out means, asked before it goes.
    static func removeWarning(_ provider: API.AiProvider) -> String {
        switch provider {
        case .anthropic: String(localized: "AI is off unless the environment gives a key.")
        case .local: String(localized: "Your model server is asked with no key from then on.")
        case .compatible: String(localized: "The service is asked with no key from then on.")
        default: String(localized: "AI is off until another key is kept.")
        }
    }
}

/// The AI pane, as the page's AI settings (#49): AI mode's switch, and only while it is on, the connection (the
/// provider and its key, the model and the service's URL, and how AI stands), then what AI may spend and write ahead.
/// Each control saves on its own. The models a provider offers, and the model servers running on this Mac, are asked
/// for only as a person acts here (turns AI mode on, picks a provider, keeps a key, gives a URL, opens the menu,
/// presses refresh or Look Again) or as the pane opens with AI mode on, never in the background.
struct AIPane: View {
    let status: AppStatus
    @State private var kept: Set<API.AiProvider>?
    @State private var listed: AIChoices.Listed?
    @State private var asking = AIChoices.Asking()
    /// What looking for model servers on this Mac came to, once asked.
    @State private var look: AIChoices.Look?
    /// A model being typed by hand, while its field is open.
    @State private var typedModel: String?
    /// The URL as it is being typed, until it is kept.
    @State private var typedURL: String?
    @Environment(\.meadow) private var meadow

    init(status: AppStatus, kept: Set<API.AiProvider>? = nil, listed: AIChoices.Listed? = nil,
         look: AIChoices.Look? = nil)
    {
        self.status = status
        _kept = State(initialValue: kept)
        _listed = State(initialValue: listed)
        _look = State(initialValue: look)
    }

    private var preferences: API.Preferences? { status.preferences }
    private var provider: API.AiProvider { preferences?.aiProvider ?? .anthropic }
    /// AI mode, off until a person turns it on.
    private var enabled: Bool { preferences?.aiEnabled ?? false }

    var body: some View {
        SettingsPage(tab: .ai) {
            PreferencesRead(status: status) {
                ForEach(AIChoices.parts(aiMode: enabled, provider: provider), id: \.self) { part($0) }
            }
        }
        .task(id: preferences == nil) { await opened() }
        .onChange(of: enabled) { _, on in
            if on { Task { await ask(provider) } }
        }
    }

    @ViewBuilder private func part(_ part: AIChoices.Part) -> some View {
        switch part {
        case .mode:
            DeskSideCard(title: String(localized: "AI"), icon: .wind) {
                SettingSwitch(String(localized: "AI mode"), help: String(localized: """
                    Summaries, the brief, drafts and Ask. Off, MailMind sends nothing to any AI service.
                    """), isOn: status.preference(\.aiEnabled) { .init(aiEnabled: $0) })
                SettingHelp(AIWords.sends(preferences, status.digest?.ai))
            }
        case .connection:
            DeskSideCard(title: String(localized: "Connection"), icon: .link) {
                SettingLine(String(localized: "Provider")) {
                    SettingChoice(label: String(localized: "Provider"), title: provider.label, width: 300) {
                        [.title(String(localized: "Provider"))] + API.AiProvider.allCases.map {
                            choice -> MeadowMenu.Part in
                            .item(MeadowMenuItem(id: choice.rawValue, title: choice.label,
                                                 check: choice == provider) { choose(choice) })
                        }
                    }
                }
            }
        case .found:
            DeskSideCard(title: String(localized: "On this Mac"), icon: .search) {
                foundServers
            }
        case .key:
            DeskSideCard(title: AIChoices.keyLabel(provider), icon: .key) {
                AIKeyView(status: status, provider: provider, kept: $kept) {
                    Task { await list(provider, unlessForeseen: true) }
                }
            }
        case .model:
            DeskSideCard(title: provider == .local ? String(localized: "Model and server")
                                : provider.asksForURL ? String(localized: "Model and service")
                                : String(localized: "Model"), icon: .sliders) {
                modelLine
                if provider.asksForURL { urlLine }
            }
        case .standing: standing
        case .spending: spending
        case .writtenAhead: writtenAhead
        }
    }

    // MARK: The model and the URL

    private var defaultModel: String? {
        AIChoices.defaultModel(provider, listed: listed, preferences: preferences, ai: status.digest?.ai)
    }

    @ViewBuilder private var modelLine: some View {
        let chosen = preferences?.aiModel ?? ""
        // With no model server yet, the context is a step of getting one, said there.
        SettingLine(String(localized: "Model"),
                    help: provider == .local && look == .found([]) ? nil : AIChoices.modelHelp(provider)) {
            HStack(spacing: 6) {
                SettingChoice(label: String(localized: "Model"),
                              title: AIChoices.title(provider, chosen: chosen, defaultModel: defaultModel), width: 320,
                              opened: {
                                  if listed?.provider != provider, asking.provider != provider {
                                      Task { await list(provider) }
                                  }
                              }) {
                    AIChoices.modelMenu(provider, chosen: chosen, defaultModel: defaultModel, listed: listed,
                                        asking: asking.provider == provider, choose: { chooseModel($0) },
                                        type: { typedModel = chosen })
                }
                Button { Task { await list(provider) } } label: { MeadowIconView(.refresh, size: 15) }
                    .meadowButton(.quiet, small: true, icon: true)
                    .help("Ask \(provider.name) for its models again")
                    .accessibilityLabel("Ask \(provider.name) for its models again")
                    .disabled(asking.provider == provider)
            }
        }
        if let typed = typedModel {
            HStack(spacing: 6) {
                TextField("Model", text: Binding(get: { typed }, set: { typedModel = $0 }),
                          prompt: Text("the model’s name, as the provider writes it"))
                    .labelsHidden()
                    .meadowComposeField()
                    .onSubmit { keepTypedModel() }
                Button("Use") { keepTypedModel() }
                    .meadowButton(.primary, small: true)
                    .disabled(typed.trimmingCharacters(in: .whitespaces).isEmpty)
                Button("Cancel") { typedModel = nil }
                    .meadowButton(.quiet, small: true)
            }
        }
        if let problem = listed.flatMap({ $0.provider == provider ? $0.problem : nil }) {
            Text(problem).font(.meadow(.ui, 12.5, .semibold)).foregroundStyle(meadow.rust)
        }
    }

    @ViewBuilder private var urlLine: some View {
        let shown = AIChoices.shownURL(provider, preferences: preferences)
        let typed = typedURL ?? shown
        SettingLine(provider == .local ? String(localized: "Server URL") : String(localized: "Service URL"),
                    help: AIChoices.urlHelp(provider)) {
            HStack(spacing: 6) {
                TextField(provider == .local ? "Server URL" : "Service URL",
                          text: Binding(get: { typed }, set: { typedURL = $0 }),
                          prompt: Text(verbatim: provider == .local ? API.AiProvider.localURL
                                                                    : "https://ai.example.test/v1"))
                    .labelsHidden()
                    .meadowComposeField()
                    .frame(width: 260)
                    .onSubmit { keepURL() }
                Button("Save") { keepURL() }
                    .meadowButton(.primary, small: true)
                    .fixedSize()
                    .disabled(typedURL == nil || typed == shown)
            }
        }
    }

    // MARK: What runs on this Mac

    /// The model servers found on this Mac, each by name with the models it lists to choose from; while looking, or
    /// with none found, a line saying so. Look Again asks again.
    @ViewBuilder private var foundServers: some View {
        let look = self.look ?? .looking
        if case .found(let servers) = look, !servers.isEmpty {
            ForEach(servers, id: \.url) { server in
                VStack(alignment: .leading, spacing: 2) {
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text(verbatim: server.name)
                            .font(.meadow(.ui, 14, .semibold))
                            .foregroundStyle(meadow.ink)
                        Text(verbatim: server.url)
                            .font(.meadow(.ui, 12.5))
                            .foregroundStyle(meadow.ink3)
                            .lineLimit(1)
                            .truncationMode(.middle)
                    }
                    .padding(.bottom, 4)
                    if server.models.isEmpty {
                        SettingHelp(String(localized: "It lists no model that can write yet."))
                    }
                    if server.models.count > 8 {
                        MeadowFitScroll(maxHeight: 300) { foundModels(server) }
                    } else {
                        foundModels(server)
                    }
                }
            }
        } else if let line = AIChoices.lookLine(look) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                if look == .looking { ProgressView().controlSize(.small) }
                Text(line)
                    .font(.meadow(.ui, 13.5))
                    .foregroundStyle(look == .looking ? meadow.ink3 : meadow.ink2)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if look == .found([]) { OllamaSteps() }
        }
        HStack {
            Spacer(minLength: 0)
            Button { Task { await lookOnThisMac() } } label: {
                MeadowIconView(.refresh, size: 15)
                Text("Look Again")
            }
            .meadowButton(.quiet, small: true)
            .fixedSize()
            .disabled(look == .looking)
        }
    }

    /// A server's models, each chosen with a click, the one in use ticked.
    private func foundModels(_ server: API.LocalServer) -> some View {
        VStack(spacing: 0) {
            ForEach(server.models, id: \.self) { model in
                FoundModelRow(model: model, chosen: AIChoices.isChosen(model, on: server, preferences: preferences)) {
                    chooseFound(model, on: server)
                }
            }
        }
    }

    // MARK: Spending, writing ahead and how AI stands

    private var spending: some View {
        DeskSideCard(title: String(localized: "Spending"), icon: .receipt) {
            SettingLine(String(localized: "Daily limit, in US dollars"), help: String(localized: """
                Once a day’s AI has cost this much, background work stops and MailMind asks before spending \
                more.
                """)) {
                TextField("Daily limit, in US dollars", value: limit, format: .number.precision(.fractionLength(0...2)))
                    .labelsHidden()
                    .multilineTextAlignment(.trailing)
                    .meadowComposeField()
                    .frame(width: 88)
            }
            if let ai = status.digest?.ai, ai.isOn, let spent = ai.spentTodayMicros {
                MeadowFact(String(localized: "Today"), last: true) {
                    Text("\(StatusWords.dollars(spent)) of \(StatusWords.dollars(ai.limitMicros))")
                }
            }
            if let ai = status.digest?.ai {
                if let models = AIWords.modelsUsed(ai) { SettingHelp(models) }
                if let waiting = AIWords.summariesWaiting(ai.summariesWaiting) { SettingHelp(waiting) }
            }
            SpentLately(status: status, provider: provider)
        }
    }

    private var writtenAhead: some View {
        DeskSideCard(title: String(localized: "Written ahead"), icon: .wind) {
            SettingSwitch(String(localized: "Have today’s brief ready each morning"), help: String(localized: """
                Written before you open your Inbox, within the daily limit, and only if you’ve used MailMind in the \
                last two days.
                """), isOn: status.preference(\.morningBrief) { .init(morningBrief: $0) })
            SettingSwitch(String(localized: "Have last week’s review ready each Monday"), help: String(localized: """
                Written on Monday morning, within the daily limit, and only if you’ve used MailMind in the last two \
                days. Your week shows it until the next Monday, and ↻ there writes a new one.
                """) + " " + AIWords.reviewGoes(provider, url: preferences?.aiBaseUrl ?? "",
                                                   cloudModel: AIWords.cloudModel(status.digest?.ai, chosen: provider)),
                          isOn: status.preference(\.weeklyReview) { .init(weeklyReview: $0) })
            SettingSwitch(String(localized: "File low-priority mail by itself, once a day"), help: String(localized: """
                Only what today’s brief offers to tidy. The brief says what went, and one click takes it all back.
                """), isOn: status.preference(\.autoTidy) { .init(autoTidy: $0) })
            SettingSwitch(String(localized: "Draft replies ahead for important mail"), help: String(localized: """
                Written while you’re using MailMind, within the daily limit, so a reply is ready when you open the \
                message. Off, a reply is drafted only when you ask for one.
                """) + " " + AIWords.aheadGoes(provider, url: preferences?.aiBaseUrl ?? "",
                                                   cloudModel: AIWords.cloudModel(status.digest?.ai, chosen: provider)),
                          isOn: status.preference(\.draftAhead) { .init(draftAhead: $0) })
        }
    }

    /// How AI stands, as the digest says: on with which model, off and why, or the last call that failed.
    @ViewBuilder private var standing: some View {
        if let ai = status.digest?.ai {
            let said = AIWords.standing(ai)
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                SettingDot(colour: said.failing ? meadow.rust : ai.isOn ? meadow.moss : meadow.ink3)
                    .alignmentGuide(.firstTextBaseline) { $0[.bottom] - 3 }
                Text(said.text)
                    .font(.meadow(.ui, 13.5, .semibold))
                    .foregroundStyle(said.failing ? meadow.rust : meadow.ink2)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.horizontal, 4)
            .accessibilityElement(children: .combine)
        }
    }

    /// No bounds of its own: the server holds the limit to more than nothing and its most, and says them when it
    /// refuses one.
    private var limit: Binding<Double> {
        Preference(status: status, read: \.aiDailyLimitUsd, change: { .init(aiDailyLimitUsd: $0) }, fallback: 0).binding
    }

    // MARK: What a person does

    /// The pane is open: which keys are kept, and with AI on, what the provider offers.
    private func opened() async {
        guard preferences != nil, let core = status.core else { return }
        if kept == nil, let read = try? await core.get(API.Get.AiKey.self) { kept = Set(read.kept) }
        if enabled { await ask(provider) }
    }

    /// Ask what `asked` offers, as AI mode turns on, the pane opens or the provider is chosen: what runs on this Mac
    /// for a model there, else the provider's models where nothing would refuse.
    private func ask(_ asked: API.AiProvider) async {
        if asked == .local {
            await lookOnThisMac()
        } else {
            await list(asked, unlessForeseen: true)
        }
    }

    /// Choose another provider: it starts from its own model and URL, and what it offers is asked for.
    private func choose(_ chosen: API.AiProvider) {
        guard chosen != provider else { return }
        typedModel = nil
        typedURL = nil
        let saved = status.setPreferences(AIChoices.choose(chosen))
        Task {
            await saved?.value
            await ask(chosen)
        }
    }

    private func chooseModel(_ model: String) {
        typedModel = nil
        status.setPreferences(.init(aiModel: model))
    }

    /// A model a server on this Mac listed: its server's URL and the model, in one change.
    private func chooseFound(_ model: String, on server: API.LocalServer) {
        typedModel = nil
        typedURL = nil
        let changes = AIChoices.choose(model, on: server)
        // Another server's list isn't this one's: the model menu asks again when it opens.
        if changes.aiBaseUrl != preferences?.aiBaseUrl { listed = nil }
        status.setPreferences(changes)
    }

    /// Ask the core which model servers run on this Mac, and what each lists. It asks each for a second at most,
    /// sends no key, and finds none rather than fail.
    private func lookOnThisMac() async {
        guard let core = status.core, look != .looking else { return }
        look = .looking
        look = await AIChoices.look { try await core.get(API.Get.AiLocal.self) }
    }

    private func keepTypedModel() {
        guard let typed = typedModel?.trimmingCharacters(in: .whitespacesAndNewlines), !typed.isEmpty else { return }
        chooseModel(typed)
    }

    private func keepURL() {
        guard let typed = typedURL else { return }
        typedURL = nil
        let saved = status.setPreferences(.init(aiBaseUrl: AIChoices.url(typed: typed, provider: provider)))
        let asked = provider
        Task {
            await saved?.value
            await list(asked, unlessForeseen: true)
        }
    }

    /// Ask the provider which models it offers. `unlessForeseen`: not where the server would refuse for want of a
    /// key or a URL, which the key's card and the URL's line already say. An ask made while another is on its way
    /// goes at once, a provider chosen or a key or URL kept meanwhile being what the pane shows now, and only the
    /// latest ask's answer is kept.
    private func list(_ asked: API.AiProvider, unlessForeseen: Bool = false) async {
        guard let core = status.core else { return }
        if unlessForeseen,
           !AIChoices.canList(asked, kept: kept, preferences: preferences, sources: .ofThisProcess)
        {
            asking.drop()
            return
        }
        let turn = asking.begin(asked)
        let answer: AIChoices.Listed
        do {
            answer = AIChoices.Listed(provider: asked,
                                      list: try await core.get(API.Get.AiModels.self, ["provider": asked.rawValue]))
        } catch {
            answer = AIChoices.Listed(provider: asked, problem: (error as? Problem)?.detail
                                      ?? String(localized: "Couldn’t ask for the models."))
        }
        if asking.end(turn) { listed = answer }
    }
}

/// What AI cost over the last 7 days and this month, by feature, as the server counts it (`/api/ai/spending`), and
/// where the bill is. Asked for as the card shows, and again as today's spending changes.
private struct SpentLately: View {
    let status: AppStatus
    let provider: API.AiProvider
    @State private var spent: API.Spending?

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            if let spent {
                SettingHelp(AIWords.spent(String(localized: "The last 7 days"), spent.week))
                SettingHelp(AIWords.spent(String(localized: "This month"), spent.month))
                SettingHelp(AIWords.billOfRecord(provider))
            }
        }
        .task(id: status.digest?.ai.spentTodayMicros) {
            guard let core = status.core else { return }
            if let read = try? await core.get(API.Get.AiSpending.self) { spent = read }
        }
    }
}

/// The steps to a model on this Mac for a person with no model server, as the page has them, with Ollama's site a
/// click away in the person's browser.
private struct OllamaSteps: View {
    @Environment(\.meadow) private var meadow

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(Array(AIChoices.firstSteps.enumerated()), id: \.offset) { index, step in
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(verbatim: "\(index + 1).").monospacedDigit().foregroundStyle(meadow.ink3)
                    Text(step).foregroundStyle(meadow.ink2).fixedSize(horizontal: false, vertical: true)
                }
                .font(.meadow(.ui, 13))
            }
            Button { AppSeams.links.open(AIChoices.getOllama) } label: {
                Text("Get Ollama")
                MeadowIconView(.external, size: 14)
            }
            .meadowButton(small: true)
            .fixedSize()
            .help(AIChoices.getOllama.absoluteString)
        }
    }
}

/// A model a server on this Mac listed, to choose with a click: ticked while it is the one chosen.
private struct FoundModelRow: View {
    let model: String
    let chosen: Bool
    let choose: () -> Void

    var body: some View {
        Button(action: choose) {
            MeadowPaletteRow(model, icon: nil, pop: true, check: chosen)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(chosen ? String(localized: "\(model), chosen") : model)
    }
}

/// A provider's API key: how it stands and where the one in use comes from, a field to keep one, a way to take it out,
/// and where its keys are made. A model on this Mac and a compatible service need none, and take one only if asked.
struct AIKeyView: View {
    let status: AppStatus
    let provider: API.AiProvider
    @Binding var kept: Set<API.AiProvider>?
    /// A key was kept: the pane asks the provider what it offers.
    var keptOne: () -> Void = {}

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            KeptSecretView(
                label: AIChoices.keyLabel(provider),
                prompt: AIChoices.keyPrompt(provider),
                standing: kept.map {
                    SecretStanding.apiKey(provider, kept: $0.contains(provider), ai: status.digest?.ai,
                                          sources: .ofThisProcess)
                },
                keep: keep, remove: clear,
                removeWarning: AIChoices.removeWarning(provider))
                // Another provider's field starts empty.
                .id(provider)
            if let page = provider.keyPage, let place = provider.keyPlace {
                HStack(spacing: 8) {
                    SettingHelp(String(localized: "\(provider.name)’s keys are made at \(place)."))
                    Spacer(minLength: 8)
                    Button { AppSeams.links.open(page) } label: {
                        Text("Open")
                        MeadowIconView(.external, size: 14)
                    }
                    .meadowButton(small: true)
                    .fixedSize()
                    .help(page.absoluteString)
                    .accessibilityLabel("Open \(place)")
                }
            }
            if let billing = provider.billing {
                HStack(spacing: 8) {
                    SettingHelp(billing)
                    Spacer(minLength: 8)
                    if let page = provider.creditPage {
                        Button { AppSeams.links.open(page) } label: {
                            Text("Add Credit")
                            MeadowIconView(.external, size: 14)
                        }
                        .meadowButton(small: true)
                        .fixedSize()
                        .help(page.absoluteString)
                        .accessibilityLabel("Add credit at \(page.host() ?? "")")
                    }
                }
            }
        }
        .task { if kept == nil { await read() } }
    }

    private func read() async {
        guard let core = status.core else { return }
        if let read = try? await core.get(API.Get.AiKey.self) { kept = Set(read.kept) }
    }

    private func keep(_ typed: String) async -> String? {
        guard let core = status.core else { return nil }
        do {
            let answer = try await SecretPost.post(core, API.Post.AiKey.self,
                                                   API.KeyBody(key: typed, provider: provider))
            kept = Set(answer.kept)
            await status.aiChanged()
            status.say(String(localized: "\(AIChoices.keyLabel(provider)) kept in your Keychain"))
            keptOne()
            return nil
        } catch {
            return (error as? Problem)?.description ?? String(localized: "Couldn’t keep it")
        }
    }

    private func clear() async -> String? {
        guard let core = status.core else { return nil }
        do {
            kept = Set(try await core.post(API.Post.AiKeyClear.self, API.ProviderBody(provider: provider)).kept)
            await status.aiChanged()
            return nil
        } catch {
            return (error as? Problem)?.description ?? String(localized: "Couldn’t remove it")
        }
    }
}
#endif
