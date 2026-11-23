#if os(macOS)
import Foundation

struct EncryptedMailboxProfile: Codable, Sendable {
    let origin: String
    let token: String
    let mailbox: String
    let privateKey: Data

    var valid: Bool {
        guard let url = URLComponents(string: origin), url.path.isEmpty, url.query == nil,
              url.fragment == nil, url.user == nil, url.password == nil,
              token.wholeMatch(of: /[a-f0-9]{64}/) != nil,
              mailbox.wholeMatch(of: /[A-Za-z0-9_-]{1,64}/) != nil, !privateKey.isEmpty else { return false }
        if url.scheme == "https", let host = url.host, !host.isEmpty, url.port == nil || url.port == 443 {
            return true
        }
        return url.scheme == "http" && url.host == "127.0.0.1" && url.port != nil
    }

    static func readFile(_ url: URL) throws -> Self {
        let properties = try url.resourceValues(forKeys: [.isRegularFileKey, .fileSizeKey])
        guard properties.isRegularFile == true, let size = properties.fileSize,
              size > 0, size <= 64_000 else { throw HostedMailboxIssue.profile }
        let data = try Data(contentsOf: url, options: .mappedIfSafe)
        guard data.count <= 64_000 else { throw HostedMailboxIssue.profile }
        let profile = try JSONDecoder().decode(Self.self, from: data)
        guard profile.valid else { throw HostedMailboxIssue.profile }
        return profile
    }
}


enum HostedMailboxIssue: LocalizedError {
    case profile, unauthorized

    var errorDescription: String? {
        switch self {
        case .profile: "The HodHod mailbox profile could not be read."
        case .unauthorized: "The HodHod mailbox session was rejected."
        }
    }
}
#endif
