#if os(macOS)
import CryptoKit
import Foundation
import LocalAuthentication
import Security

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

enum EncryptedMailboxKeychain {
    private static var service: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: "com.hodhod.mail.encrypted-pilot",
         kSecAttrAccount as String: Bundle.main.bundleIdentifier ?? "hodhod"]
    }

    static func load() -> EncryptedMailboxProfile? {
        var query = service
        query[kSecReturnData as String] = true
        let authentication = LAContext()
        authentication.interactionNotAllowed = true
        query[kSecUseAuthenticationContext as String] = authentication
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data,
              let profile = try? JSONDecoder().decode(EncryptedMailboxProfile.self, from: data),
              profile.valid else { return nil }
        return profile
    }

    static func save(_ profile: EncryptedMailboxProfile) throws {
        let attributes = [kSecValueData as String: try JSONEncoder().encode(profile)]
        let status = SecItemUpdate(service as CFDictionary, attributes as CFDictionary)
        if status == errSecSuccess { return }
        guard status == errSecItemNotFound,
              SecItemAdd(service.merging(attributes) { _, new in new } as CFDictionary, nil) == errSecSuccess else {
            throw HostedMailboxIssue.keychain
        }
    }
}

enum HostedMailboxIssue: LocalizedError {
    case profile, corrupt, keychain, unauthorized, unreachable, relay, conflict
    var errorDescription: String? {
        switch self {
        case .profile: "The encrypted mailbox profile could not be read."
        case .corrupt: "This message could not be authenticated and decrypted."
        case .keychain: "The mailbox profile could not be saved in Keychain."
        case .unauthorized: "The mailbox session expired or was rejected."
        case .unreachable: "The hosted mailbox could not be reached."
        case .relay: "External sending is unavailable. Your draft is still here."
        case .conflict: "This send conflicts with a previous submission. Your draft is still here."
        }
    }
}

enum HostedEnvelope {
    static func open(_ data: Data, profile: EncryptedMailboxProfile, id: String, keyVersion: UInt32) throws -> Data {
        guard data.count >= 34, data.prefix(4) == Data("HHE1".utf8) else { throw HostedMailboxIssue.corrupt }
        let wrappedSize = Int(data[16]) * 256 + Int(data[17])
        let body = 18 + wrappedSize
        guard wrappedSize >= 256, body + 16 <= data.count else { throw HostedMailboxIssue.corrupt }
        let attributes = [kSecAttrKeyType as String: kSecAttrKeyTypeRSA,
                          kSecAttrKeyClass as String: kSecAttrKeyClassPrivate]
        guard let privateKey = SecKeyCreateWithData(profile.privateKey as CFData, attributes as CFDictionary, nil),
              wrappedSize == SecKeyGetBlockSize(privateKey),
              let unwrapped = SecKeyCreateDecryptedData(privateKey, .rsaEncryptionOAEPSHA256,
                  data.subdata(in: 18..<body) as CFData, nil) as Data?, unwrapped.count == 32 else {
            throw HostedMailboxIssue.corrupt
        }
        let nonce = try AES.GCM.Nonce(data: data.subdata(in: 4..<16))
        let sealed = try AES.GCM.SealedBox(nonce: nonce, ciphertext: data[body..<(data.count - 16)], tag: data.suffix(16))
        let authenticated = Data("hodhod-message-v1/\(profile.mailbox)/\(keyVersion)/\(id)".utf8)
        do {
            return try AES.GCM.open(sealed, using: SymmetricKey(data: unwrapped), authenticating: authenticated)
        } catch {
            throw HostedMailboxIssue.corrupt
        }
    }
}
#endif
