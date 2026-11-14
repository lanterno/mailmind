#if os(macOS)
import Foundation

/// The routes as the mailbox calls them: a GET or a POST as its generated types, a refusal thrown as its `Problem`,
/// over one function that answers any request as the core would. The app's is the core's own (`Core.send`); a test's
/// answers what the test says, so the mailbox's verbs are tested without a core.
struct Wire: Sendable {
    let send: @Sendable (_ method: String, _ target: String, _ body: Data) async -> CoreAnswer

    init(send: @escaping @Sendable (_ method: String, _ target: String, _ body: Data) async -> CoreAnswer) {
        self.send = send
    }

    init(_ core: Core) {
        send = { method, target, body in await core.send(method, target, body) }
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

    /// The bytes a route answers that aren't JSON, by the URL the server names them with (a document's download).
    func bytes(at target: String) async throws -> NotJSON {
        guard target.hasPrefix("/api/") else { throw Problem(CoreAnswer(status: 400, contentType: "", headers: [], body: Data())) }
        let answer = await send("GET", target, Data())
        guard (200..<300).contains(answer.status) else { throw Problem(answer) }
        return NotJSON(data: answer.body, contentType: answer.contentType)
    }
}
#endif
