import Foundation
import XCTest

/// The routes as a test answers them: each path's answer written as JSON, and every call kept with its body.
final class FakeRoutes: Routes, @unchecked Sendable {
    struct Call {
        let path: String
        let query: [String: String]
        let body: JSONValue
        /// Sent with the person's leave to go over today's AI limit.
        let overLimit: Bool
    }

    private let lock = NSLock()
    private var answers: [String: @Sendable (JSONValue) throws -> String] = [:]
    private var made: [Call] = []

    /// What `path` answers: JSON made from the body it was sent, or thrown.
    func answer(_ path: String, _ make: @escaping @Sendable (JSONValue) throws -> String) {
        lock.withLock { answers[path] = make }
    }

    func answer(_ path: String, _ json: String) {
        answer(path) { _ in json }
    }

    var calls: [Call] { lock.withLock { made } }

    func calls(to path: String) -> [Call] { calls.filter { $0.path == path } }

    func get<R: GetRoute>(_ route: R.Type, _ query: KeyValuePairs<String, String?>) async throws -> R.Answer
        where R.Answer: Decodable
    {
        var asked: [String: String] = [:]
        for (name, value) in query { if let value { asked[name] = value } }
        return try reply(R.path, asked, .null)
    }

    func post<R: PostRoute>(_ route: R.Type, _ body: R.Body) async throws -> R.Answer where R.Answer: Decodable {
        let sent = try JSONDecoder().decode(JSONValue.self, from: JSONEncoder().encode(body))
        return try reply(R.path, [:], sent)
    }

    func postOverLimit<R: PostRoute>(_ route: R.Type, _ body: R.Body) async throws -> R.Answer
        where R.Answer: Decodable
    {
        let sent = try JSONDecoder().decode(JSONValue.self, from: JSONEncoder().encode(body))
        return try reply(R.path, [:], sent, overLimit: true)
    }

    private func reply<A: Decodable>(_ path: String, _ query: [String: String], _ body: JSONValue,
                                     overLimit: Bool = false) throws -> A
    {
        let make = lock.withLock { () -> (@Sendable (JSONValue) throws -> String)? in
            made.append(Call(path: path, query: query, body: body, overLimit: overLimit))
            return answers[path]
        }
        guard let make else { throw FakeRoutes.problem(404, "Not found") }
        return try JSONDecoder().decode(A.self, from: Data(try make(body).utf8))
    }

    /// A refusal as the core would send it.
    static func problem(_ status: UInt16, _ title: String) -> Problem {
        Problem(CoreAnswer(status: status, contentType: "application/problem+json", headers: [],
                           body: Data(#"{"title": "\#(title)", "status": \#(status)}"#.utf8)))
    }

    /// Today's AI limit reached, as the server says it: $15.25 spent of $15.
    static let overLimit = Problem(CoreAnswer(status: 402, contentType: "application/problem+json", headers: [], body: Data(#"""
        {"type": "/problems/over-limit", "title": "Over the daily AI limit", "status": 402,
         "detail": "today's AI spending has reached the daily limit", "over_limit": true,
         "spent_micros": 15250000, "limit_micros": 15000000}
        """#.utf8)))
}

extension JSONValue {
    /// A field of an object, or nil.
    subscript(_ name: String) -> JSONValue? {
        if case .object(let fields) = self { return fields[name] }
        return nil
    }

    var string: String? {
        if case .string(let value) = self { return value }
        return nil
    }

    var number: Double? {
        if case .number(let value) = self { return value }
        return nil
    }

    var strings: [String]? {
        if case .array(let items) = self { return items.compactMap(\.string) }
        return nil
    }
}

/// Wait, a little at a time, until `done` holds, or fail after a while.
@MainActor
func eventually(_ what: String = "it to happen", within seconds: Double = 3, file: StaticString = #filePath,
                line: UInt = #line, _ done: () -> Bool) async
{
    let end = Date().addingTimeInterval(seconds)
    while !done() {
        if Date() > end {
            XCTFail("waited for \(what)", file: file, line: line)
            return
        }
        try? await Task.sleep(for: .milliseconds(20))
    }
}

/// A message as `/api/message/body` opens it, with only what a test names.
func opened(account: String = "ada@example.test", from: String = "bo@example.test", to: String = "ada@example.test",
            cc: String = "", replyTo: String = "", subject: String = "Plans", text: String = "Hello",
            attachments: [API.AttachmentPart] = [], key: Int = 7) -> API.MessageOpened
{
    API.MessageOpened(account: account, attachments: attachments, blockedCssUrls: 0, blockedImages: 0, cc: cc,
                      date: "Mon, 2 Nov 2026 12:00", folder: "INBOX", fromAddr: from, fromName: "Bo", hasHtml: false,
                      key: key, messageId: "<m\(key)@example.test>", ok: true, replyTo: replyTo, subject: subject,
                      text: text, to: to, uid: key)
}
