#if os(macOS)
import CryptoKit
import Foundation

struct EncryptedMailboxCache {
    struct Entry: Codable, Identifiable {
        let id: String
        var folder: String
        let received: Int64
        let keyVersion: UInt32
        var read: Bool
    }
    struct Index: Codable {
        var address: String?
        var folders: [EncryptedMailboxAPI.Folder] = []
        var entries: [String: Entry] = [:]
        var cursor: UInt64 = 0
        var draft: HostedDraft?
    }

    private let directory: URL
    private let key: SymmetricKey

    init(_ profile: EncryptedMailboxProfile, root: URL) throws {
        let identity = Data("\(profile.origin)/\(profile.mailbox)".utf8)
        key = HKDF<SHA256>.deriveKey(inputKeyMaterial: SymmetricKey(data: profile.privateKey),
            salt: identity, info: Data("hodhod-encrypted-local-cache-v1".utf8), outputByteCount: 32)
        let digest = SHA256.hash(data: identity + profile.privateKey).map { String(format: "%02x", $0) }.joined()
        directory = root.appending(path: digest, directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o700])
    }

    private var indexURL: URL { directory.appending(path: "index.hhc") }

    func load() throws -> Index {
        guard FileManager.default.fileExists(atPath: indexURL.path) else { return Index() }
        let sealed = try AES.GCM.SealedBox(combined: Data(contentsOf: indexURL))
        return try JSONDecoder().decode(Index.self, from: AES.GCM.open(sealed, using: key))
    }

    func save(_ index: Index) throws {
        guard let combined = try AES.GCM.seal(JSONEncoder().encode(index), using: key).combined else {
            throw HostedMailboxIssue.corrupt
        }
        try combined.write(to: indexURL, options: .atomic)
    }

    func read(_ id: String) throws -> Data? {
        guard id.wholeMatch(of: /[a-f0-9]{64}/) != nil else { throw HostedMailboxIssue.corrupt }
        let url = directory.appending(path: id)
        guard FileManager.default.fileExists(atPath: url.path) else { return nil }
        return try Data(contentsOf: url)
    }

    func store(_ data: Data, id: String) throws {
        guard id.wholeMatch(of: /[a-f0-9]{64}/) != nil else { throw HostedMailboxIssue.corrupt }
        try data.write(to: directory.appending(path: id), options: .atomic)
    }

    func remove(_ id: String) {
        guard id.wholeMatch(of: /[a-f0-9]{64}/) != nil else { return }
        try? FileManager.default.removeItem(at: directory.appending(path: id))
    }
}

struct HostedDraft: Codable, Equatable {
    var to = ""
    var subject = ""
    var body = ""
    var replyToID: String?
    var references: String?
    var files: [HostedDraftFile] = []
    var pending: Pending?
    var submission: String?

    struct Pending: Codable, Equatable {
        let key: String
        let payload: Data
    }
}

struct HostedDraftFile: Codable, Equatable, Identifiable {
    let id: UUID
    let name: String
    let bytes: Data
}

enum HostedMessageWriter {
    static func payload(_ draft: HostedDraft, from: String) throws -> Data {
        let to = draft.to.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        let address = /[a-z0-9._+-]+@[a-z0-9.-]+/
        guard to.wholeMatch(of: address) != nil, from.wholeMatch(of: address) != nil,
              to.utf8.count <= 254, draft.subject.utf8.count <= 150,
              draft.body.utf8.count <= 256_000 else {
            throw HostedMailboxIssue.profile
        }
        let tag = UUID().uuidString.lowercased()
        let encodedSubject = Data(draft.subject.utf8).base64EncodedString()
        var headers = ["From: \(from)", "To: \(to)", "Subject: =?UTF-8?B?\(encodedSubject)?=",
                       "Message-ID: <\(tag)@\(from.split(separator: "@").last ?? "hodhod")>", "MIME-Version: 1.0"]
        let date = DateFormatter()
        date.locale = Locale(identifier: "en_US_POSIX")
        date.dateFormat = "EEE, dd MMM yyyy HH:mm:ss Z"
        headers.append("Date: \(date.string(from: Date()))")
        if let replyID = draft.replyToID.flatMap(messageID) {
            headers.append("In-Reply-To: \(replyID)")
            let refs = (draft.references ?? "").split(whereSeparator: \.isWhitespace).suffix(8)
                .compactMap { messageID(String($0)) }
            headers.append("References: " + (refs + [replyID]).joined(separator: " "))
        }
        let part = "Content-Type: text/plain; charset=utf-8\r\nContent-Transfer-Encoding: base64\r\n\r\n"
            + encodePart(Data(draft.body.utf8))
        let mime: String
        if draft.files.isEmpty {
            mime = headers.joined(separator: "\r\n") + "\r\n" + part + "\r\n"
        } else {
            headers.append("Content-Type: multipart/mixed; boundary=\"\(tag)\"")
            var components = [part]
            let filenameChars = CharacterSet(charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.")
            for file in draft.files {
                let escaped = file.name.addingPercentEncoding(withAllowedCharacters: filenameChars) ?? "attachment"
                components.append("Content-Type: application/octet-stream\r\nContent-Disposition: attachment; filename*=UTF-8''\(escaped)"
                                  + "\r\nContent-Transfer-Encoding: base64\r\n\r\n" + encodePart(file.bytes))
            }
            mime = headers.joined(separator: "\r\n") + "\r\n\r\n--\(tag)\r\n"
                + components.joined(separator: "\r\n--\(tag)\r\n") + "\r\n--\(tag)--\r\n"
        }
        guard mime.utf8.count <= 3 * 1024 * 1024 else { throw CocoaError(.fileWriteOutOfSpace) }
        return try JSONSerialization.data(withJSONObject: [
            "from": from, "recipients": [to], "mime": Data(mime.utf8).base64EncodedString(),
        ], options: [.sortedKeys])
    }

    private static func encodePart(_ bytes: Data) -> String {
        bytes.base64EncodedString(options: [.lineLength76Characters, .endLineWithCarriageReturn, .endLineWithLineFeed])
    }

    private static func messageID(_ value: String) -> String? {
        let trimmed = value.trimmingCharacters(in: CharacterSet(charactersIn: "<> \t"))
        guard trimmed.utf8.count < 140, trimmed.wholeMatch(of: /[A-Za-z0-9._+!$=-]+@[A-Za-z0-9.-]+/) != nil else { return nil }
        return "<\(trimmed)>"
    }
}
#endif
