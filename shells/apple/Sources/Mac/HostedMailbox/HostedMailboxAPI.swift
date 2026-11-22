#if os(macOS)
import Foundation

private final class EncryptedMailboxRedirects: NSObject, URLSessionTaskDelegate, Sendable {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping @Sendable (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}

struct EncryptedMailboxAPI: Sendable {
    struct AccountList: Decodable { let mailboxes: [Account] }
    struct Account: Decodable { let id: String; let address: String? }
    struct FolderList: Decodable { let folders: [Folder] }
    struct Folder: Codable, Identifiable, Sendable { let id: String; let name: String; let role: String? }
    struct Page: Decodable { let changes: [Change]; let cursor: UInt64; let hasMore: Bool }
    struct Change: Decodable {
        let id: String
        let kind: String
        let action: String
        let folderId: String?
        let receivedAt: Int64?
        let keyVersion: UInt32?
        let isRead: Int?
    }
    struct Submission: Decodable {
        let id: String
        let state: String
        let failureReason: String?
    }

    let profile: EncryptedMailboxProfile
    private static let session = URLSession(configuration: .ephemeral, delegate: EncryptedMailboxRedirects(), delegateQueue: nil)
    private var mailboxPath: String { "/v2/mailboxes/" + profile.mailbox }

    func account() async throws -> String {
        let result = try await read(AccountList.self, path: "/v2/mailboxes")
        guard let mailbox = result.mailboxes.first(where: { $0.id == profile.mailbox }), let address = mailbox.address else {
            throw HostedMailboxIssue.unauthorized
        }
        return address
    }

    func folders() async throws -> [Folder] {
        try await read(FolderList.self, path: mailboxPath + "/folders").folders
    }

    func changes(after cursor: UInt64) async throws -> Page {
        try await read(Page.self, path: mailboxPath + "/changes", query: [URLQueryItem(name: "after", value: "\(cursor)")])
    }

    func ciphertext(_ id: String) async throws -> Data {
        guard id.wholeMatch(of: /[a-f0-9]{64}/) != nil else { throw HostedMailboxIssue.corrupt }
        return try await data(mailboxPath + "/messages/" + id)
    }

    func move(_ id: String, folder: String? = nil, read: Bool? = nil) async throws {
        var fields: [String: Any] = [:]
        if let folder { fields["folderId"] = folder }
        if let read { fields["isRead"] = read }
        _ = try await data(mailboxPath + "/messages/" + id, method: "PATCH",
                           payload: JSONSerialization.data(withJSONObject: fields))
    }

    func send(_ payload: Data, key: String) async throws -> Submission {
        try await read(Submission.self, path: mailboxPath + "/submissions", method: "POST", payload: payload, key: key)
    }

    func submission(_ id: String) async throws -> Submission {
        guard id.wholeMatch(of: /[a-f0-9]{64}/) != nil else { throw HostedMailboxIssue.unreachable }
        return try await read(Submission.self, path: mailboxPath + "/submissions/" + id)
    }

    private func read<T: Decodable>(_ type: T.Type, path: String, query: [URLQueryItem] = [],
                                    method: String = "GET", payload: Data? = nil, key: String? = nil) async throws -> T {
        let response = try await data(path, query: query, method: method, payload: payload, key: key)
        return try JSONDecoder().decode(type, from: response)
    }

    private func data(_ path: String, query: [URLQueryItem] = [], method: String = "GET", payload: Data? = nil,
                      key: String? = nil) async throws -> Data {
        guard var url = URLComponents(string: profile.origin) else { throw HostedMailboxIssue.profile }
        url.path = path
        if !query.isEmpty { url.queryItems = query }
        guard let endpoint = url.url else { throw HostedMailboxIssue.profile }
        var request = URLRequest(url: endpoint, timeoutInterval: 20)
        request.httpMethod = method
        request.setValue("Bearer " + profile.token, forHTTPHeaderField: "Authorization")
        request.setValue("HodHod-Mac/0.2", forHTTPHeaderField: "User-Agent")
        if let key { request.setValue(key, forHTTPHeaderField: "Idempotency-Key") }
        if let payload {
            request.httpBody = payload
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (bytes, response) = try await Self.session.data(for: request)
        switch (response as? HTTPURLResponse)?.statusCode {
        case 200, 202: return bytes
        case 401: throw HostedMailboxIssue.unauthorized
        case 409: throw HostedMailboxIssue.conflict
        case 503 where method == "POST" &&
            (try? JSONDecoder().decode([String: String].self, from: bytes))?["error"] == "approved_relay_unavailable":
            throw HostedMailboxIssue.relay
        default: throw HostedMailboxIssue.unreachable
        }
    }
}
#endif
