#if os(macOS)
import Foundation

/// The routes as the mailbox calls them: a GET or a POST as its generated types, a refusal thrown as its `Problem`,
/// over one function that answers any request as the core would. The app's is the core's own (`Core.send`), and asks
/// with the window's alert at the day's AI limit; a test's answers what the test says, so the mailbox's verbs are
/// tested without a core.
struct Wire: OverLimitRoutes {
    typealias Send = @Sendable (_ method: String, _ target: String, _ body: Data) async -> CoreAnswer

    let send: Send
    /// `send` with a person's leave to go over today's AI limit; a test's wire without one sends as `send` does.
    let sendOverLimit: Send
    /// What asks them for it when an AI call they started reaches the limit: the window's alert in the app. A test's
    /// wire asks nothing unless it says, so a call there at the limit only says so.
    let asksOverLimit: AskOverLimit?

    init(send: @escaping Send, sendOverLimit: Send? = nil, asksOverLimit: AskOverLimit? = nil) {
        self.send = send
        self.sendOverLimit = sendOverLimit ?? send
        self.asksOverLimit = asksOverLimit
    }

    init(_ core: Core) {
        send = { method, target, body in await core.send(method, target, body) }
        sendOverLimit = { method, target, body in await core.send(method, target, body, overLimit: true) }
        asksOverLimit = { await OverLimitQuestion.onScreen.ask($0) }
    }

    /// What `route` answers to a GET with `query`.
    func get<R: GetRoute>(_ route: R.Type, _ query: KeyValuePairs<String, String?> = [:]) async throws -> R.Answer
        where R.Answer: Decodable
    {
        try Core.decoded(R.Answer.self, from: await send("GET", R.path + Core.query(query), Data()))
    }

    /// What `route` answers to a POST of `body`.
    func post<R: PostRoute>(_ route: R.Type, _ body: R.Body) async throws -> R.Answer where R.Answer: Decodable {
        try Core.decoded(R.Answer.self, from: await send("POST", R.path, try JSONEncoder().encode(body)))
    }

    /// `post`, with the person's leave to go over today's AI limit.
    func postOverLimit<R: PostRoute>(_ route: R.Type, _ body: R.Body) async throws -> R.Answer
        where R.Answer: Decodable
    {
        try Core.decoded(R.Answer.self, from: await sendOverLimit("POST", R.path, try JSONEncoder().encode(body)))
    }

    /// The bytes a route answers that aren't JSON, by the URL the server names them with (a document's download).
    func bytes(at target: String) async throws -> NotJSON {
        guard target.hasPrefix("/api/") else { throw Problem(CoreAnswer(status: 400, contentType: "", headers: [], body: Data())) }
        let answer = await send("GET", target, Data())
        guard (200..<300).contains(answer.status) else { throw Problem(answer) }
        return NotJSON(data: answer.body, contentType: answer.contentType)
    }
}
#endif
