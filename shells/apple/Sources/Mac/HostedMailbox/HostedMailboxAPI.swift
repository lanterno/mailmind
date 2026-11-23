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
    let profile: EncryptedMailboxProfile

    private static let session = URLSession(configuration: .ephemeral, delegate: EncryptedMailboxRedirects(), delegateQueue: nil)

    func account() async throws -> String {
        guard var url = URLComponents(string: profile.origin) else { throw HostedMailboxIssue.profile }
        url.path = "/v2/mailboxes"
        guard let endpoint = url.url else { throw HostedMailboxIssue.profile }
        var request = URLRequest(url: endpoint, timeoutInterval: 15)
        request.setValue("Bearer " + profile.token, forHTTPHeaderField: "Authorization")
        request.setValue("HodHod-Mac/0.2", forHTTPHeaderField: "User-Agent")
        let (bytes, response) = try await Self.session.data(for: request)
        guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw HostedMailboxIssue.unauthorized }
        let result = try JSONDecoder().decode(AccountList.self, from: bytes)
        guard let address = result.mailboxes.first(where: { $0.id == profile.mailbox })?.address else {
            throw HostedMailboxIssue.unauthorized
        }
        return address
    }
}
#endif
