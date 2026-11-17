import AppKit
import Foundation
import XCTest

private typealias F = StatusFixtures

/// The AI pane's choices (#49): what each provider asks for, the model menu's order, and what the pane sends.
final class AIChoicesTests: XCTestCase {
    private func listed(_ provider: API.AiProvider, default model: String?, _ models: [String]) -> AIChoices.Listed {
        AIChoices.Listed(provider: provider, list: API.ModelList(default: model, models: models, ok: true))
    }

    func testWhatEachProviderAsksFor() {
        XCTAssertEqual(API.AiProvider.allCases.map(\.label),
                       ["Claude (Anthropic)", "OpenAI", "Google Gemini", "A model on this Mac",
                        "Another OpenAI-compatible service"])
        XCTAssertEqual(API.AiProvider.allCases.filter(\.needsKey), [.anthropic, .openai, .gemini],
                       "a model on this Mac's key, and a compatible service's, is only if the server asks")
        XCTAssertEqual(API.AiProvider.allCases.filter(\.asksForURL), [.local, .compatible])
        XCTAssertEqual(API.AiProvider.allCases.filter(\.needsModel), [.local, .compatible])
        XCTAssertEqual(API.AiProvider.anthropic.keyPlace, "console.anthropic.com")
        XCTAssertEqual(API.AiProvider.openai.keyPlace, "platform.openai.com/api-keys")
        XCTAssertEqual(API.AiProvider.gemini.keyPlace, "aistudio.google.com/apikey")
        XCTAssertNil(API.AiProvider.local.keyPage)
        XCTAssertNil(API.AiProvider.compatible.keyPage)
        XCTAssertEqual(AIChoices.keyLabel(.openai), "OpenAI API key")
        XCTAssertEqual(AIChoices.keyLabel(.compatible), "API key")
        XCTAssertEqual(AIChoices.keyLabel(.local), "API key")
        XCTAssertEqual(AIChoices.keyPrompt(.local), "paste your server’s key")
        XCTAssertEqual(SecretStanding.apiKey(.local, kept: false, ai: nil, sources: SecretSources()),
                       SecretStanding(summary: "No key", note: "Only if your server asks for one.", kept: false))
    }

    /// A model on this Mac is said as one, never as the server it runs in; Ollama is named only as a server found, and
    /// in the steps to one and the context it needs.
    func testAModelOnThisMacIsSaidAsOne() {
        XCTAssertEqual(API.AiProvider.local.name, "your model server")
        XCTAssertEqual(API.AiProvider.local.writer, "a model on this Mac")
        XCTAssertEqual(API.AiProvider.local.title, "A model on this Mac")
        XCTAssertEqual(API.AiProvider.openai.title, "OpenAI")
        XCTAssertEqual(API.AiProvider.compatible.title, "Your AI service")
        XCTAssertEqual(AIChoices.modelHelp(.local), "In Ollama’s settings, set Context length to 16K or more.")
        XCTAssertEqual(AIChoices.urlHelp(.local),
                       "Where your model server listens, on this Mac: 127.0.0.1, ::1 or localhost.")
        for provider in API.AiProvider.allCases {
            for words in [provider.label, provider.name, provider.writer,
                          AIChoices.urlHelp(provider), AIChoices.keyLabel(provider), AIChoices.keyPrompt(provider),
                          AIChoices.removeWarning(provider), AIWords.sends(provider)] {
                XCTAssertFalse(words.contains("Ollama"), "\(provider): \(words)")
            }
        }
    }

    /// AI mode off, the pane is its switch alone; on, the connection by provider, how it stands, then spending and
    /// writing ahead.
    func testThePanesPartsFollowAIMode() {
        for provider in API.AiProvider.allCases {
            XCTAssertEqual(AIChoices.parts(aiMode: false, provider: provider), [.mode], "\(provider)")
        }
        XCTAssertEqual(AIChoices.parts(aiMode: true, provider: .anthropic),
                       [.mode, .connection, .key, .model, .standing, .spending, .writtenAhead])
        XCTAssertEqual(AIChoices.parts(aiMode: true, provider: .compatible),
                       [.mode, .connection, .key, .model, .standing, .spending, .writtenAhead])
        XCTAssertEqual(AIChoices.parts(aiMode: true, provider: .local),
                       [.mode, .connection, .found, .model, .key, .standing, .spending, .writtenAhead],
                       "what runs on this Mac first, and the key, which only some servers ask for, after the model")
    }

    func testChoosingAProviderSendsItAlone() {
        let sent = AIChoices.choose(.gemini)
        XCTAssertEqual(sent.aiProvider, .gemini)
        XCTAssertNil(sent.aiModel, "the server starts it from its own model")
        XCTAssertNil(sent.aiBaseUrl)
        XCTAssertNil(sent.aiEnabled)
    }

    /// The default first, named where it is known; a model typed by hand next; then the rest of what was listed.
    func testTheModelMenusOrder() {
        let list = listed(.openai, default: "gpt-fictional", ["gpt-fictional-mini", "gpt-fictional", "gpt-fictional-nano"])
        let model = AIChoices.defaultModel(.openai, listed: list, preferences: F.preferences(provider: "openai"),
                                           ai: nil)
        XCTAssertEqual(model, "gpt-fictional")
        XCTAssertEqual(AIChoices.options(.openai, chosen: "", defaultModel: model, listed: list).map(\.title),
                       ["Default (gpt-fictional)", "gpt-fictional-mini", "gpt-fictional-nano"])
        XCTAssertEqual(AIChoices.options(.openai, chosen: "gpt-by-hand", defaultModel: model, listed: list).map(\.id),
                       ["", "gpt-by-hand", "gpt-fictional-mini", "gpt-fictional-nano"])
        XCTAssertEqual(AIChoices.options(.openai, chosen: "gpt-fictional-nano", defaultModel: model, listed: list)
                           .map(\.id), ["", "gpt-fictional-mini", "gpt-fictional-nano"], "a listed one isn't twice")
        XCTAssertEqual(AIChoices.title(.openai, chosen: "", defaultModel: model), "Default (gpt-fictional)")
        XCTAssertEqual(AIChoices.title(.openai, chosen: "gpt-fictional-nano", defaultModel: model), "gpt-fictional-nano")
    }

    private func isScroll(_ part: MeadowMenu.Part) -> Bool {
        if case .scroll = part { true } else { false }
    }

    private func key(_ code: UInt16) -> NSEvent {
        NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: [], timestamp: 0, windowNumber: 0, context: nil,
                         characters: "", charactersIgnoringModifiers: "", isARepeat: false, keyCode: code)!
    }

    /// A provider listing many (OpenAI and Gemini list dozens): they scroll, every one a line the keys go over, and
    /// choosing one, by Return or a click, closes the menu.
    @MainActor func testALongModelListIsChosenFromTheKeysAndClosesTheMenu() {
        let models = (1...30).map { "gpt-fictional-\($0)" }
        var chosen: [String] = []
        let parts = AIChoices.modelMenu(.openai, chosen: "", defaultModel: "gpt-fictional-1",
                                        listed: listed(.openai, default: "gpt-fictional-1", models), asking: false,
                                        choose: { chosen.append($0) }, type: {})
        XCTAssertEqual(parts.filter(isScroll).count, 1)
        let items = MeadowMenu.items(parts)
        XCTAssertEqual(items.map(\.id), ["model-"] + models.dropFirst().map { "model-\($0)" } + ["type"])

        let menu = MeadowMenuModel()
        var closed = 0
        menu.close = { closed += 1 }
        menu.items = items
        for _ in 0..<12 { XCTAssertTrue(menu.key(key(125))) }
        XCTAssertTrue(menu.key(key(36)))
        XCTAssertEqual(chosen, ["gpt-fictional-13"])
        XCTAssertEqual(closed, 1)
        menu.run(items[20])
        XCTAssertEqual(chosen.last, "gpt-fictional-21")
        XCTAssertEqual(closed, 2, "a click closes it too")
    }

    /// A few models are lines of their own; while the provider is being asked, the menu says so, as no line to choose.
    @MainActor func testAFewModelsAreLinesOfTheirOwnAndTheMenuSaysWhileItAsks() {
        let list = listed(.openai, default: "gpt-fictional", ["gpt-fictional-mini", "gpt-fictional"])
        func menu(asking: Bool) -> [MeadowMenu.Part] {
            AIChoices.modelMenu(.openai, chosen: "", defaultModel: "gpt-fictional", listed: list, asking: asking,
                                choose: { _ in }, type: {})
        }
        func says(_ parts: [MeadowMenu.Part]) -> Bool {
            parts.contains { if case .item(let item) = $0 { item.id == "listing" && item.disabled } else { false } }
        }
        XCTAssertTrue(menu(asking: false).filter(isScroll).isEmpty)
        XCTAssertEqual(MeadowMenu.items(menu(asking: true)).map(\.id), ["model-", "model-gpt-fictional-mini", "type"])
        XCTAssertTrue(says(menu(asking: true)))
        XCTAssertFalse(says(menu(asking: false)))
    }

    func testAnotherProvidersListIsntThisOnes() {
        let list = listed(.gemini, default: "gemini-fictional", ["gemini-fictional"])
        XCTAssertNil(AIChoices.defaultModel(.openai, listed: list, preferences: nil, ai: nil))
        XCTAssertEqual(AIChoices.options(.openai, chosen: "", defaultModel: nil, listed: list).map(\.title),
                       ["Default"])
    }

    /// Before a list, the default is named only where the digest says it is the one in force.
    func testTheDefaultIsNamedFromTheDigestWhileItIsInForce() {
        let on = F.digest(provider: "openai", model: "gpt-fictional").ai
        XCTAssertEqual(AIChoices.defaultModel(.openai, listed: nil, preferences: F.preferences(provider: "openai"),
                                              ai: on), "gpt-fictional")
        XCTAssertNil(AIChoices.defaultModel(.openai, listed: nil,
                                            preferences: F.preferences(provider: "openai", model: "gpt-by-hand"),
                                            ai: on), "a model chosen by hand isn't the default")
        XCTAssertNil(AIChoices.defaultModel(.gemini, listed: nil, preferences: F.preferences(provider: "gemini"),
                                            ai: on), "OpenAI's model isn't Gemini's default")
        let off = F.digest(engine: "none", off: "turned-off").ai
        XCTAssertNil(AIChoices.defaultModel(.anthropic, listed: nil, preferences: F.preferences(), ai: off))
    }

    /// A model on this Mac and a compatible service have no default: a model must be chosen, and the menu says so.
    func testAModelOnThisMacAndACompatibleServiceHaveNoDefault() {
        let list = listed(.local, default: nil, ["llama-fictional", "mistral-fictional"])
        let model = AIChoices.defaultModel(.local, listed: list, preferences: F.preferences(provider: "local"),
                                           ai: nil)
        XCTAssertNil(model)
        XCTAssertEqual(AIChoices.options(.local, chosen: "", defaultModel: model, listed: list).map(\.id),
                       ["llama-fictional", "mistral-fictional"])
        XCTAssertEqual(AIChoices.title(.local, chosen: "", defaultModel: nil), "Choose a model")
        XCTAssertEqual(AIChoices.title(.compatible, chosen: "", defaultModel: nil), "Choose a model")
        XCTAssertEqual(AIChoices.options(.compatible, chosen: "by-hand", defaultModel: nil, listed: nil).map(\.id),
                       ["by-hand"])
    }

    /// The models are asked for without a person pressing only where no refusal is foreseen.
    func testWhenTheModelsCanBeAskedFor() {
        let none = SecretSources()
        XCTAssertTrue(AIChoices.canList(.local, kept: [], preferences: F.preferences(provider: "local"),
                                        sources: none))
        XCTAssertFalse(AIChoices.canList(.compatible, kept: [], preferences: F.preferences(provider: "compatible"),
                                         sources: none), "no URL yet")
        XCTAssertTrue(AIChoices.canList(.compatible, kept: [],
                                        preferences: F.preferences(provider: "compatible",
                                                                   baseURL: "https://ai.example.test/v1"),
                                        sources: none))
        XCTAssertFalse(AIChoices.canList(.anthropic, kept: [], preferences: F.preferences(), sources: none))
        XCTAssertTrue(AIChoices.canList(.anthropic, kept: [.anthropic], preferences: F.preferences(), sources: none))
        XCTAssertTrue(AIChoices.canList(.anthropic, kept: [], preferences: F.preferences(),
                                        sources: SecretSources(apiKeyVariable: true)))
        XCTAssertFalse(AIChoices.canList(.openai, kept: [.anthropic], preferences: F.preferences(provider: "openai"),
                                         sources: SecretSources(apiKeyVariable: true)), "the variable is Claude's")
        XCTAssertTrue(AIChoices.canList(.gemini, kept: [.gemini], preferences: nil, sources: none))
        XCTAssertFalse(AIChoices.canList(.gemini, kept: nil, preferences: nil, sources: none), "not read yet")
    }

    /// A provider chosen while another's models are on their way is asked at once, and only its answer is kept.
    func testTheLatestAskForTheModelsStands() {
        var asking = AIChoices.Asking()
        let openai = asking.begin(.openai)
        XCTAssertEqual(asking.provider, .openai)
        let gemini = asking.begin(.gemini)
        XCTAssertEqual(asking.provider, .gemini, "the menu says Gemini is being asked, as it is")
        XCTAssertFalse(asking.end(openai), "OpenAI's answer, come late, is let go")
        XCTAssertEqual(asking.provider, .gemini, "Gemini is still being asked")
        XCTAssertTrue(asking.end(gemini))
        XCTAssertNil(asking.provider)

        let before = asking.begin(.compatible)
        asking.drop()
        XCTAssertNil(asking.provider)
        XCTAssertFalse(asking.end(before), "a refusal foreseen lets go of what was asked before it")
    }

    /// A model on this Mac's URL is shown filled in, Ollama's; kept as typed, that one is "", so it goes on
    /// following the provider's default.
    func testTheServicesURL() {
        XCTAssertEqual(AIChoices.shownURL(.local, preferences: F.preferences(provider: "local")),
                       "http://127.0.0.1:11434/v1")
        XCTAssertEqual(AIChoices.shownURL(.compatible, preferences: F.preferences(provider: "compatible")), "")
        XCTAssertEqual(AIChoices.shownURL(.local, preferences: F.preferences(provider: "local",
                                                                              baseURL: "http://localhost:9999/v1")),
                       "http://localhost:9999/v1")
        XCTAssertEqual(AIChoices.url(typed: " http://127.0.0.1:11434/v1 ", provider: .local), "")
        XCTAssertEqual(AIChoices.url(typed: "http://localhost:9999/v1", provider: .local), "http://localhost:9999/v1")
        XCTAssertEqual(AIChoices.url(typed: "https://ai.example.test/v1\n", provider: .compatible),
                       "https://ai.example.test/v1")
    }
}

/// Finding a model on this Mac (#49): what looking came to, what the card says of it, and what choosing a model a
/// server listed sends.
@MainActor
final class LocalFinderTests: XCTestCase {
    static let ollama = API.LocalServer(models: ["llama-fictional", "qwen-fictional"], name: "Ollama",
                                        url: "http://127.0.0.1:11434/v1")
    static let studio = API.LocalServer(models: ["mistral-fictional"], name: "LM Studio", url: "http://127.0.0.1:1234/v1")

    func testFoundSeveral() async {
        let look = await AIChoices.look { API.LocalServers(ok: true, servers: [Self.ollama, Self.studio]) }
        XCTAssertEqual(look, .found([Self.ollama, Self.studio]), "in the core's order")
        XCTAssertNil(AIChoices.lookLine(look), "the card lists them, and says nothing over them")
    }

    func testFoundNone() async {
        let look = await AIChoices.look { API.LocalServers(ok: true, servers: []) }
        XCTAssertEqual(look, .found([]))
        XCTAssertEqual(AIChoices.lookLine(look), """
            No model server is running on this Mac yet.
            """)
        XCTAssertEqual(AIChoices.firstSteps, [
            "Get Ollama and open it.",
            "In Ollama, download a model. A good first one is qwen3:8b, as Ollama lists it: it runs well on a Mac with 16 GB of memory.",
            "In Ollama’s settings, set Context length to 16K or more.",
            "Then look again.",
        ], "the steps to a model on this Mac, as the page has them")
        XCTAssertEqual(AIChoices.getOllama.absoluteString, "https://ollama.com")
    }

    func testLooking() {
        XCTAssertEqual(AIChoices.lookLine(.looking), "Looking for model servers on this Mac…")
    }

    func testTheAskingFailed() async {
        let refused = await AIChoices.look {
            throw Problem(CoreAnswer(status: 503, contentType: "application/problem+json", headers: [], body: Data(#"""
                {"type": "about:blank", "title": "Unavailable", "status": 503, "detail": "The core is starting."}
                """#.utf8)))
        }
        XCTAssertEqual(refused, .failed("The core is starting."))
        XCTAssertEqual(AIChoices.lookLine(refused), "The core is starting.")
        let broken = await AIChoices.look { throw URLError(.cannotConnectToHost) }
        XCTAssertEqual(broken, .failed("Couldn’t look for model servers."))
    }

    /// The server's URL and the model go in one change: Ollama's URL as "", the provider's own, any other's as it is.
    func testChoosingAFoundModelSendsItsServerAndTheModelAtOnce() {
        let atOllama = AIChoices.choose("qwen-fictional", on: Self.ollama)
        XCTAssertEqual(atOllama.aiBaseUrl, "", "Ollama's URL is the provider's own")
        XCTAssertEqual(atOllama.aiModel, "qwen-fictional")
        XCTAssertNil(atOllama.aiProvider, "a model on this Mac is the provider already")
        XCTAssertNil(atOllama.aiEnabled)
        XCTAssertTrue(AppStatus.touchesAI(atOllama), "how AI stands is read again")
        let atStudio = AIChoices.choose("mistral-fictional", on: Self.studio)
        XCTAssertEqual(atStudio.aiBaseUrl, "http://127.0.0.1:1234/v1")
        XCTAssertEqual(atStudio.aiModel, "mistral-fictional")
        let held = F.preferences(provider: "local", model: "llama-fictional")
        let merged = try? XCTUnwrap(AppStatus.merged(held, atStudio))
        XCTAssertEqual(merged?.aiBaseUrl, "http://127.0.0.1:1234/v1")
        XCTAssertEqual(merged?.aiModel, "mistral-fictional")
        XCTAssertEqual(merged?.aiProvider, .local)
    }

    /// The one ticked is the model chosen at the server it was found at.
    func testTheModelChosenIsTicked() {
        let atOllama = F.preferences(provider: "local", model: "llama-fictional")
        XCTAssertTrue(AIChoices.isChosen("llama-fictional", on: Self.ollama, preferences: atOllama),
                      "\"\" is Ollama's URL")
        XCTAssertFalse(AIChoices.isChosen("qwen-fictional", on: Self.ollama, preferences: atOllama))
        let sameName = API.LocalServer(models: ["llama-fictional"], name: "LM Studio", url: Self.studio.url)
        XCTAssertFalse(AIChoices.isChosen("llama-fictional", on: sameName, preferences: atOllama),
                       "the same name at another server isn't the one chosen")
        let atStudio = F.preferences(provider: "local", model: "mistral-fictional",
                                     baseURL: "http://127.0.0.1:1234/v1/")
        XCTAssertTrue(AIChoices.isChosen("mistral-fictional", on: Self.studio, preferences: atStudio))
        let elsewhere = F.preferences(provider: "compatible", model: "llama-fictional")
        XCTAssertFalse(AIChoices.isChosen("llama-fictional", on: Self.ollama, preferences: elsewhere))
        XCTAssertFalse(AIChoices.isChosen("llama-fictional", on: Self.ollama, preferences: nil))
    }
}

/// What the app says of AI: whom a message goes to, how AI stands, and why it is off.
final class AIWordsTests: XCTestCase {
    /// What AI mode sends is said before it is turned on: what goes to the provider by itself, what goes only when asked,
    /// and that attachments never leave; with a model on this Mac, or a service answering here, nothing leaves.
    func testWhatAIModeSendsIsSaidBeforeItIsOn() {
        XCTAssertEqual(AIWords.sends(.anthropic), """
            With AI mode on, the sender, subject and opening text of each message in your Inbox go to Anthropic to be \
            summarized, automatically: the last 30 days’ as soon as you turn it on, then each new one. Those senders, \
            subjects and summaries go again to write the day’s brief and what came in since you looked. Whole \
            conversations go only when you ask: for a summary or a draft, with what you type into Ask, a search written \
            as a sentence, or Rewrite. Attachments never leave this Mac.
            """)
        XCTAssertTrue(AIWords.sends(.openai).contains("each message in your Inbox go to OpenAI to be summarized"))
        XCTAssertTrue(AIWords.sends(.gemini).contains("each message in your Inbox go to Google to be summarized"))
        XCTAssertTrue(AIWords.sends(.compatible).contains("go to the service at the URL you gave to be summarized"))
        XCTAssertTrue(AIWords.sends(.compatible, url: "https://ai.example.test/v1").contains("go to the service"))
        XCTAssertEqual(AIWords.sends(.local), "With a model on this Mac, nothing leaves this Mac.")
        XCTAssertEqual(AIWords.sends(.compatible, url: "http://localhost:8080/v1"),
                       "With the service on this Mac, nothing leaves this Mac.")
        XCTAssertEqual(AIWords.sends(.local, url: "http://127.0.0.1:1234/v1"),
                       "With a model on this Mac, nothing leaves this Mac.")
        for provider in API.AiProvider.allCases where provider != .anthropic {
            XCTAssertFalse(AIWords.sends(provider).contains("Anthropic"), "\(provider)")
        }
    }

    /// Draft replies ahead sends whole conversations of important mail, and says so wherever what goes is said, and
    /// under its own switch, naming whom they go to.
    func testDraftRepliesAheadSaysWholeConversationsGo() {
        let ahead = "With Draft replies ahead on, whole conversations of important mail go too, to draft replies before "
            + "you open them."
        XCTAssertEqual(AIWords.sends(.openai, draftAhead: true), AIWords.sends(.openai) + " " + ahead)
        XCTAssertFalse(AIWords.sends(.openai).contains("Draft replies ahead"), "off, it isn't said")
        XCTAssertEqual(AIWords.sends(.local, draftAhead: true), "With a model on this Mac, nothing leaves this Mac.")

        XCTAssertEqual(AIWords.aheadGoes(.gemini), "On, whole conversations of important mail go to Google to draft them.")
        XCTAssertEqual(AIWords.aheadGoes(.compatible, url: "https://ai.example.test/v1"),
                       "On, whole conversations of important mail go to the service at the URL you gave to draft them.")
        XCTAssertEqual(AIWords.aheadGoes(.local), "On, they are drafted on this Mac, and nothing leaves it.")
        XCTAssertEqual(AIWords.aheadGoes(.local, cloudModel: true),
                       "On, whole conversations of important mail go to Ollama’s servers to draft them.")
    }

    /// The weekly review written ahead sends more than the Inbox each Monday: its switch says what, in the page's
    /// words, and to whom; nothing, with a model on this Mac.
    func testTheWeeklyReviewsSwitchSaysWhatGoesEachMonday() {
        let review = "On, each Monday the senders, subjects and summaries of mail that needs you, the subjects and "
            + "recipients of threads you’re waiting on, and the names of the week’s meetings go to"
        XCTAssertEqual(AIWords.reviewGoes(.anthropic), "\(review) Anthropic to write it.")
        XCTAssertEqual(AIWords.reviewGoes(.openai), "\(review) OpenAI to write it.")
        XCTAssertEqual(AIWords.reviewGoes(.gemini), "\(review) Google to write it.")
        XCTAssertEqual(AIWords.reviewGoes(.compatible, url: "https://llm.example.test/v1"),
                       "\(review) the service at the URL you gave to write it.")
        XCTAssertEqual(AIWords.reviewGoes(.local, cloudModel: true), "\(review) Ollama’s servers to write it.")
        XCTAssertEqual(AIWords.reviewGoes(.compatible, url: "http://localhost:11434/v1", cloudModel: true),
                       "\(review) Ollama’s servers to write it.")
        XCTAssertEqual(AIWords.reviewGoes(.local), "On, it is written on this Mac, and nothing leaves it.")
        XCTAssertEqual(AIWords.reviewGoes(.compatible, url: "http://localhost:8080/v1"),
                       "On, it is written on this Mac, and nothing leaves it.")
    }

    /// A model on this Mac whose model runs in Ollama's cloud doesn't say nothing leaves: it says what goes, and where.
    func testAModelInOllamasCloudSaysWhatLeaves() {
        XCTAssertEqual(AIWords.sends(.local, model: "gpt-oss:120b-cloud", cloudModel: true),
                       "gpt-oss:120b-cloud runs in Ollama’s cloud, so what is summarized goes to Ollama’s servers.")
        XCTAssertEqual(AIWords.sends(.compatible, url: "http://localhost:11434/v1", model: "qwen-fictional-cloud",
                                     cloudModel: true),
                       "qwen-fictional-cloud runs in Ollama’s cloud, so what is summarized goes to Ollama’s servers.")
        XCTAssertTrue(AIWords.sends(.local, cloudModel: true).hasPrefix("The model chosen runs in Ollama’s cloud"))
        XCTAssertTrue(AIWords.sends(.local, model: "m-cloud", cloudModel: true, draftAhead: true)
            .hasSuffix("goes to Ollama’s servers. With Draft replies ahead on, whole conversations of important mail go "
                       + "too, to draft replies before you open them."))
    }

    /// The status says a model runs in Ollama's cloud only of the provider in use: a provider just chosen on this
    /// Mac, not yet in use, isn't said to send to Ollama because the one in use did, as the page's `sendsOf` holds.
    func testACloudModelIsSaidOnlyOfTheProviderInUse() {
        var ai = StatusFixtures.digest().ai
        ai.provider = .local
        ai.cloudModel = true
        XCTAssertTrue(AIWords.cloudModel(ai, chosen: .local))
        XCTAssertFalse(AIWords.cloudModel(ai, chosen: .compatible))
        XCTAssertFalse(AIWords.cloudModel(nil, chosen: .local))
        let chosen = StatusFixtures.preferences(provider: "compatible", baseURL: "http://localhost:8080/v1")
        XCTAssertFalse(AIWords.sends(chosen, ai).contains("Ollama’s cloud"))
    }

    /// Settings, the first run, Help and the brief say it from the preferences and the digest alike.
    func testWhatIsSentIsSaidFromThePreferencesAndTheDigest() {
        typealias F = StatusFixtures
        let local = F.preferences(provider: "local", model: "llama-fictional-cloud", draftAhead: true)
        var ai = F.digest().ai
        ai.cloudModel = true
        ai.provider = .local
        XCTAssertEqual(AIWords.sends(local, ai), AIWords.sends(.local, model: "llama-fictional-cloud", cloudModel: true,
                                                               draftAhead: true))
        ai.cloudModel = false
        XCTAssertEqual(AIWords.sends(local, ai), "With a model on this Mac, nothing leaves this Mac.")
        let gemini = F.preferences(provider: "gemini", draftAhead: true)
        XCTAssertTrue(AIWords.sends(gemini, ai).hasSuffix("to draft replies before you open them."))
        XCTAssertTrue(AIWords.sends(gemini, ai).contains("go to Google to be summarized"))
        XCTAssertEqual(AIWords.sends(nil, nil), AIWords.sends(.anthropic))
    }

    /// Help says the same of the provider chosen, whether AI mode is on or off.
    @MainActor func testHelpSaysWhatAIModeSends() {
        let help = HelpWindow.howAI(sends: AIWords.sends(.gemini))
        XCTAssertTrue(help.contains("nothing is sent. " + AIWords.sends(.gemini) + " Your mailbox itself stays"), help)
        XCTAssertTrue(HelpWindow.howAI(sends: AIWords.sends(.local))
            .contains("With a model on this Mac, nothing leaves this Mac."))
    }

    /// A paid key's card says it is billed per use by its provider, apart from any chat subscription, and where credit
    /// is added; Gemini's, that its project is billed. A model on this Mac and a compatible service say nothing of it.
    func testEachKeyCardSaysHowItIsBilled() {
        XCTAssertEqual(API.AiProvider.anthropic.billing, """
            Billed per use by Anthropic, separately from any ChatGPT or Claude subscription. Add credit in \
            Anthropic’s console, under Billing.
            """)
        XCTAssertEqual(API.AiProvider.openai.billing, """
            Billed per use by OpenAI, separately from any ChatGPT or Claude subscription. Add credit on OpenAI’s \
            platform, under Billing.
            """)
        XCTAssertEqual(API.AiProvider.gemini.billing, """
            Billed per use by Google, separately from any ChatGPT or Claude subscription. Use a key on a project with \
            billing set up: on the free tier, Google may use what is sent, your mail included, to improve its \
            products, and a key stops each day at its quota.
            """, "Google's terms let it use what a free key sends")
        XCTAssertEqual(API.AiProvider.anthropic.creditPage?.absoluteString, "https://console.anthropic.com/settings/billing")
        XCTAssertEqual(API.AiProvider.openai.creditPage?.absoluteString,
                       "https://platform.openai.com/settings/organization/billing")
        for provider in [API.AiProvider.gemini, .local, .compatible] { XCTAssertNil(provider.creditPage, "\(provider)") }
        XCTAssertNil(API.AiProvider.local.billing)
        XCTAssertNil(API.AiProvider.compatible.billing)
    }

    /// On this Mac is http or https at 127.0.0.1, ::1 or localhost, as the core holds a model on this Mac to.
    func testWhatIsOnThisMac() {
        for url in ["http://127.0.0.1:11434/v1", "https://localhost:1234/v1", "http://[::1]:8080/v1",
                    " HTTP://LOCALHOST/v1 "] {
            XCTAssertTrue(AIWords.onThisMac(url), url)
        }
        for url in ["", "https://ai.example.test/v1", "http://127.0.0.2:11434/v1", "ftp://localhost/v1",
                    "http://localhost.example.test/v1", "127.0.0.1:11434"] {
            XCTAssertFalse(AIWords.onThisMac(url), url)
        }
    }

    func testHowAIStands() {
        XCTAssertEqual(AIWords.standing(F.digest(provider: "openai", model: "gpt-fictional").ai).text,
                       "AI is on: OpenAI, gpt-fictional.")
        let failing = AIWords.standing(F.digest(lastOk: false, lastError: "the key was refused",
                                                provider: "gemini", model: "gemini-fictional").ai)
        XCTAssertEqual(failing.text, "AI is on: Gemini, gemini-fictional. The last call failed: the key was refused")
        XCTAssertTrue(failing.failing)
        XCTAssertEqual(AIWords.standing(F.digest(engine: "none", off: "turned-off").ai).text,
                       "AI mode is off. MailMind sends nothing to any AI service.")
        XCTAssertEqual(AIWords.standing(F.digest(engine: "none", provider: "openai").ai).text,
                       "AI is off: there is no OpenAI API key.")
        XCTAssertEqual(AIWords.standing(F.digest(engine: "none", provider: "compatible", off: "no-url").ai).text,
                       "AI is off: the service has no URL.")
        XCTAssertEqual(AIWords.standing(F.digest(engine: "none", provider: "local", off: "no-model").ai).text,
                       "AI is off: no model is chosen.")
    }

    /// The Spending card says which model background summaries ask and which one what a person asks for goes to.
    func testTheModelsInUse() {
        let claude = F.digest(model: "claude-opus-fictional", backgroundModel: "claude-haiku-fictional").ai
        XCTAssertEqual(AIWords.modelsUsed(claude), """
            Summaries made in the background use claude-haiku-fictional; everything you ask for uses \
            claude-opus-fictional.
            """)
        let one = F.digest(provider: "local", model: "qwen-fictional").ai
        XCTAssertEqual(AIWords.modelsUsed(one),
                       "Summaries made in the background and everything you ask for use qwen-fictional.")
        XCTAssertNil(AIWords.modelsUsed(F.digest(engine: "none").ai), "no model is in use while AI is off")
    }

    /// How many messages from the last 30 days wait for a summary, as the status counts them.
    func testWhatWaitsForASummary() {
        XCTAssertNil(AIWords.summariesWaiting(nil))
        XCTAssertEqual(AIWords.summariesWaiting(0), "Every message from the last 30 days has its summary.")
        XCTAssertEqual(AIWords.summariesWaiting(1), "1 message from the last 30 days waits for a summary.")
        XCTAssertEqual(AIWords.summariesWaiting(1234), "1,234 messages from the last 30 days wait for a summary.")
        XCTAssertEqual(AIWords.summariesWaiting(F.digest(waiting: 7).ai.summariesWaiting),
                       "7 messages from the last 30 days wait for a summary.")
    }

    /// What AI cost lately: in all, its three dearest features by name and the rest summed, and where the bill is.
    func testWhatAICostLately() {
        XCTAssertEqual(AIWords.spent("The last 7 days", API.PeriodSpent(calls: 0, costMicros: 0, features: [])),
                       "The last 7 days: nothing.")
        let one = API.PeriodSpent(calls: 1, costMicros: 12_000, features: [.init(calls: 1, costMicros: 12_000, label: "Ask")])
        XCTAssertEqual(AIWords.spent("This month", one), "This month: $0.02, 1 call (Ask $0.02).")
        let month = API.PeriodSpent(calls: 1_502, costMicros: 4_310_000, features: [
            .init(calls: 1_400, costMicros: 2_000_000, label: "Summaries"),
            .init(calls: 30, costMicros: 1_200_000, label: "Today’s brief"),
            .init(calls: 40, costMicros: 800_000, label: "Drafted replies"),
            .init(calls: 30, costMicros: 300_000, label: "Ask"),
            .init(calls: 2, costMicros: 10_000, label: "Rewrite"),
        ])
        XCTAssertEqual(AIWords.spent("This month", month), """
            This month: $4.31, 1,502 calls (Summaries $2.00, Today’s brief $1.20, Drafted replies $0.80, \
            the rest $0.31).
            """)
        XCTAssertEqual(AIWords.billOfRecord(.anthropic),
                       "MailMind counts these from each call it makes; Anthropic’s console holds the bill.")
        XCTAssertEqual(AIWords.billOfRecord(.openai),
                       "MailMind counts these from each call it makes; OpenAI’s platform holds the bill.")
        XCTAssertEqual(AIWords.billOfRecord(.gemini),
                       "MailMind counts these from each call it makes; Google’s billing for your project holds the bill.")
        XCTAssertEqual(AIWords.billOfRecord(.local),
                       "MailMind counts these from each call it makes; a model on this Mac costs nothing.")
    }

    /// Off for want of something, the cards say why and where it turns on; turned off, or on, they say nothing.
    func testTheOffNoteInvitesOnlyWhereSomethingIsWanting() {
        XCTAssertNil(AIWords.offNote(nil))
        XCTAssertNil(AIWords.offNote(F.digest().ai))
        XCTAssertNil(AIWords.offNote(F.digest(engine: "none", off: "turned-off").ai), "no nag once turned off")
        let key = try? XCTUnwrap(AIWords.offNote(F.digest(engine: "none", provider: "gemini").ai))
        XCTAssertEqual(key?.why, "AI is off: there is no Gemini API key.")
        XCTAssertEqual(key?.fix, "To turn it on, keep a Gemini API key in Settings › AI.")
        XCTAssertEqual(AIWords.offNote(F.digest(engine: "none", provider: "compatible", off: "no-url").ai)?.fix,
                       "To turn it on, give the service’s URL in Settings › AI.")
        XCTAssertEqual(AIWords.offNote(F.digest(engine: "none", provider: "local", off: "no-model").ai)?.fix,
                       "To turn it on, choose a model in Settings › AI.")
    }

    func testOnAndTurnedOff() {
        XCTAssertTrue(F.digest().ai.isOn)
        XCTAssertFalse(F.digest().ai.isTurnedOff)
        let off = F.digest(engine: "none", off: "turned-off").ai
        XCTAssertFalse(off.isOn)
        XCTAssertTrue(off.isTurnedOff)
        let wanting = F.digest(engine: "none").ai
        XCTAssertFalse(wanting.isOn)
        XCTAssertFalse(wanting.isTurnedOff, "off for want of a key isn't turned off")
    }
}

/// With AI turned off, nothing only AI serves is offered: not its commands, the search field's ? and !, nor the
/// brief's week.
@MainActor
final class AITurnedOffTests: XCTestCase {
    func testTheAIsCommandsArentOffered() {
        let ids = ["snooze", "summarize", "extract", "brief", "ask", "do", "commitments", "week", "curate"]
        let all = ids.map { DeskCommand(id: $0, label: $0, key: nil, symbol: "circle") {} }
        XCTAssertEqual(Desk.offered(all, aiTurnedOff: false).map(\.id), ids)
        XCTAssertEqual(Desk.offered(all, aiTurnedOff: true).map(\.id), ["snooze", "brief", "commitments", "curate"])
    }

    /// The Assistant menu is in the menu bar whatever window is in front: with Settings, a composer or Help there no
    /// desk is focused, and the app's word that AI is turned off still hides the AI's items.
    func testTheAssistantMenuHidesTheAIsItemsWhateverWindowIsInFront() {
        let off = F.digest(engine: "none", off: "turned-off").ai
        let on = F.digest().ai
        XCTAssertFalse(DeskCommands.offersAI(nil, off), "Settings in front, just after AI was turned off there")
        XCTAssertTrue(DeskCommands.offersAI(nil, on))
        XCTAssertTrue(DeskCommands.offersAI(nil, F.digest(engine: "none").ai), "off for want of a key, still offered")
        XCTAssertTrue(DeskCommands.offersAI(nil, nil), "before the digest has come, nothing is hidden")
        XCTAssertFalse(DeskCommands.offersAI(Desk(), off))
        XCTAssertTrue(DeskCommands.offersAI(Desk(), on))
    }

    func testTheBriefsTalliesLeaveOutTheWeek() {
        XCTAssertEqual(BriefWords.tallies(needs: 1, waiting: 0, replyLater: 0, worthARead: 0, canWait: 0,
                                          commitments: 0, week: false),
                       [BriefWords.Tally(count: 1, words: "needs you", goes: .needs, sun: true)])
        XCTAssertEqual(BriefWords.tallies(needs: 0, waiting: 0, replyLater: 0, worthARead: 0, canWait: 0,
                                          commitments: 0, week: false), [])
    }

    /// The Inbox's line for the brief counts what can wait only while AI isn't turned off: the count is the AI's.
    func testTheInboxsBriefLineLeavesOutWhatCanWait() {
        XCTAssertEqual(BriefLine(needs: [], waiting: 1, canWait: nil).tally, "0 need you · 1 waiting")
        XCTAssertEqual(BriefLine(needs: [], waiting: 1, canWait: 2).tally, "0 need you · 1 waiting · 2 can wait")
    }

    func testTheSearchFieldSearchesForWhatStartsWithAQuestionMark() async {
        let app = MailApp()
        let server = Fixture.server()
        server.on("GET", API.Get.Digest.path, Fixture.digest(ai: false, off: .turnedOff))
        app.mailbox.wire = server.wire
        await app.mailbox.refresh()
        let mailbox = app.mailbox
        app.place = .folder(account: Fixture.ada, folder: "INBOX")
        XCTAssertTrue(mailbox.aiTurnedOff)
        XCTAssertFalse(mailbox.aiOn)
        XCTAssertEqual(mailbox.submitted("?what did Grace say"),
                       .search(Search(q: "?what did Grace say", account: Fixture.ada)))
        XCTAssertEqual(mailbox.submitted("!archive"), .search(Search(q: "!archive", account: Fixture.ada)))
    }

    /// A quick reply asks the AI for its line or two only while AI is on; off, the line stays and nothing is asked.
    func testAQuickReplyAsksTheAIOnlyWhileItIsOn() async throws {
        let quick = try XCTUnwrap(QuickReply.all.first)
        for (digest, asks) in [(Fixture.digest(), 1), (Fixture.digest(ai: false, off: .turnedOff), 0),
                               (Fixture.digest(ai: false, off: .noKey), 0)] {
            let (composing, routes) = try replying(digest)
            await composing.quickReply(MailCopy(Fixture.row(7)), quick)
            XCTAssertEqual(routes.calls(to: API.Post.AiDraftReply.path).count, asks, "\(digest.ai)")
            let composer = try XCTUnwrap(composing.composers.values.first)
            XCTAssertTrue(composer.body.hasPrefix(quick.line), "the line stays")
            composer.close(force: true)
        }
    }

    func testOffForWantOfAKeyTheQuestionIsStillAQuestion() async {
        let app = MailApp()
        let server = Fixture.server()
        server.on("GET", API.Get.Digest.path, Fixture.digest(ai: false, off: .noKey))
        app.mailbox.wire = server.wire
        await app.mailbox.refresh()
        XCTAssertFalse(app.mailbox.aiTurnedOff)
        XCTAssertEqual(app.mailbox.submitted("?what did Grace say"), .question("what did Grace say"),
                       "the assistant says why it can't answer")
    }

    /// The composers over routes that open message 7 for a reply, with `digest` saying how AI stands.
    private func replying(_ digest: API.Digest) throws -> (Composing, FakeRoutes) {
        let composing = Composing()
        let routes = FakeRoutes()
        routes.answer(API.Get.MessageBody.path, String(decoding: try JSONEncoder().encode(opened(key: 7)), as: UTF8.self))
        composing.use(routes, digest: digest)
        composing.openWindow = { _ in }
        return (composing, routes)
    }

    /// With AI turned off, Reply is a plain reply: no reply written ahead is asked for, offered or put in it.
    func testReplyIsPlainWithAITurnedOff() async throws {
        let (composing, routes) = try replying(Fixture.digest(ai: false, off: .turnedOff))
        let copy = MailCopy(Fixture.row(7))
        composing.viewing(copy, wanted: true, skip: false)
        composing.draftReady(7)
        await composing.answer(copy)
        XCTAssertTrue(routes.calls(to: API.Get.AiDraftAhead.path).isEmpty)
        XCTAssertTrue(routes.calls(to: API.Post.AiDraftReply.path).isEmpty)
        let composer = try XCTUnwrap(composing.composers.values.first, "a plain reply")
        XCTAssertFalse(composer.body.contains("Thursday works."))
        composer.close(force: true)
    }

    /// A reply written ahead, offered while AI was on, goes as AI is turned off; a draft that finishes after is never
    /// read, and Reply is a plain reply.
    func testAReplyWrittenAheadGoesAsAIIsTurnedOff() async throws {
        let (composing, routes) = try replying(Fixture.digest())
        routes.answer(API.Get.AiDraftAhead.path, #"{"ok": true, "status": "ready", "body": "Thursday works."}"#)
        let copy = MailCopy(Fixture.row(7))
        composing.viewing(copy, wanted: false, skip: false)
        await eventually("the reply written ahead to be offered") { composing.chips[7] == .ready }
        let asked = routes.calls(to: API.Get.AiDraftAhead.path).count

        routes.answer(API.Get.Digest.path, String(decoding: try JSONEncoder().encode(
            Fixture.digest(ai: false, off: .turnedOff)), as: UTF8.self))
        await composing.readDigest()
        XCTAssertTrue(composing.aiTurnedOff)
        XCTAssertTrue(composing.chips.isEmpty, "no Draft Ready")

        composing.draftReady(7)
        composing.viewing(copy, wanted: true, skip: false)
        await composing.answer(copy)
        try await Task.sleep(for: .milliseconds(100))
        XCTAssertEqual(routes.calls(to: API.Get.AiDraftAhead.path).count, asked, "nothing more is asked")
        XCTAssertTrue(composing.chips.isEmpty)
        let composer = try XCTUnwrap(composing.composers.values.first, "a plain reply")
        XCTAssertFalse(composer.body.contains("Thursday works."))
        composer.close(force: true)
    }
}
