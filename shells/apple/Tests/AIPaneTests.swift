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
                       ["Claude (Anthropic)", "OpenAI", "Google Gemini", "Ollama, on this Mac",
                        "Another OpenAI-compatible service"])
        XCTAssertEqual(API.AiProvider.allCases.filter(\.takesKey), [.anthropic, .openai, .gemini, .compatible],
                       "Ollama takes no key")
        XCTAssertEqual(API.AiProvider.allCases.filter(\.needsKey), [.anthropic, .openai, .gemini],
                       "a compatible service's key is its own affair")
        XCTAssertEqual(API.AiProvider.allCases.filter(\.asksForURL), [.ollama, .compatible])
        XCTAssertEqual(API.AiProvider.allCases.filter(\.needsModel), [.ollama, .compatible])
        XCTAssertEqual(API.AiProvider.anthropic.keyPlace, "console.anthropic.com")
        XCTAssertEqual(API.AiProvider.openai.keyPlace, "platform.openai.com/api-keys")
        XCTAssertEqual(API.AiProvider.gemini.keyPlace, "aistudio.google.com/apikey")
        XCTAssertNil(API.AiProvider.ollama.keyPage)
        XCTAssertNil(API.AiProvider.compatible.keyPage)
        XCTAssertEqual(AIChoices.keyLabel(.openai), "OpenAI API key")
        XCTAssertEqual(AIChoices.keyLabel(.compatible), "API key")
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

    /// Ollama and a compatible service have no default: a model must be chosen, and the menu says so.
    func testOllamaAndACompatibleServiceHaveNoDefault() {
        let list = listed(.ollama, default: nil, ["llama-fictional", "mistral-fictional"])
        let model = AIChoices.defaultModel(.ollama, listed: list, preferences: F.preferences(provider: "ollama"),
                                           ai: nil)
        XCTAssertNil(model)
        XCTAssertEqual(AIChoices.options(.ollama, chosen: "", defaultModel: model, listed: list).map(\.id),
                       ["llama-fictional", "mistral-fictional"])
        XCTAssertEqual(AIChoices.title(.ollama, chosen: "", defaultModel: nil), "Choose a model")
        XCTAssertEqual(AIChoices.title(.compatible, chosen: "", defaultModel: nil), "Choose a model")
        XCTAssertEqual(AIChoices.options(.compatible, chosen: "by-hand", defaultModel: nil, listed: nil).map(\.id),
                       ["by-hand"])
    }

    /// The models are asked for without a person pressing only where no refusal is foreseen.
    func testWhenTheModelsCanBeAskedFor() {
        let none = SecretSources()
        XCTAssertTrue(AIChoices.canList(.ollama, kept: [], preferences: F.preferences(provider: "ollama"),
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

    /// Ollama's URL is shown filled in; kept as typed, its own is "" so it goes on following the server's default.
    func testTheServicesURL() {
        XCTAssertEqual(AIChoices.shownURL(.ollama, preferences: F.preferences(provider: "ollama")),
                       "http://127.0.0.1:11434/v1")
        XCTAssertEqual(AIChoices.shownURL(.compatible, preferences: F.preferences(provider: "compatible")), "")
        XCTAssertEqual(AIChoices.shownURL(.ollama, preferences: F.preferences(provider: "ollama",
                                                                              baseURL: "http://localhost:9999/v1")),
                       "http://localhost:9999/v1")
        XCTAssertEqual(AIChoices.url(typed: " http://127.0.0.1:11434/v1 ", provider: .ollama), "")
        XCTAssertEqual(AIChoices.url(typed: "http://localhost:9999/v1", provider: .ollama), "http://localhost:9999/v1")
        XCTAssertEqual(AIChoices.url(typed: "https://ai.example.test/v1\n", provider: .compatible),
                       "https://ai.example.test/v1")
    }
}

/// What the app says of AI: whom a message goes to, how AI stands, and why it is off.
final class AIWordsTests: XCTestCase {
    func testWhatIsSentAndToWhom() {
        XCTAssertEqual(AIWords.privacy(.anthropic), """
            With AI on, the text of a message goes to Anthropic only when it is summarized, briefed or drafted; \
            nothing else leaves this Mac.
            """)
        XCTAssertTrue(AIWords.privacy(.openai).contains("goes to OpenAI only"))
        XCTAssertTrue(AIWords.privacy(.gemini).contains("goes to Google only"))
        XCTAssertTrue(AIWords.privacy(.compatible).contains("goes to the service at the URL you gave only"))
        XCTAssertTrue(AIWords.privacy(.ollama).hasPrefix("With Ollama, nothing leaves this Mac"))
        for provider in API.AiProvider.allCases where provider != .anthropic {
            XCTAssertFalse(AIWords.privacy(provider).contains("Anthropic"), "\(provider)")
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
                       "AI is off. MailMind sends nothing to any AI service.")
        XCTAssertEqual(AIWords.standing(F.digest(engine: "none", provider: "openai").ai).text,
                       "AI is off: there is no OpenAI API key.")
        XCTAssertEqual(AIWords.standing(F.digest(engine: "none", provider: "compatible", off: "no-url").ai).text,
                       "AI is off: the service has no URL.")
        XCTAssertEqual(AIWords.standing(F.digest(engine: "none", provider: "ollama", off: "no-model").ai).text,
                       "AI is off: no model is chosen.")
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
        XCTAssertEqual(AIWords.offNote(F.digest(engine: "none", provider: "ollama", off: "no-model").ai)?.fix,
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

    func testTheBriefsTalliesLeaveOutTheWeek() {
        XCTAssertEqual(BriefWords.tallies(needs: 1, waiting: 0, replyLater: 0, worthARead: 0, canWait: 0,
                                          commitments: 0, week: false),
                       [BriefWords.Tally(count: 1, words: "needs you", goes: .needs, sun: true)])
        XCTAssertEqual(BriefWords.tallies(needs: 0, waiting: 0, replyLater: 0, worthARead: 0, canWait: 0,
                                          commitments: 0, week: false), [])
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
}
