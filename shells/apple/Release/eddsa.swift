// Ed25519 for the release, on CryptoKit (docs/releasing.md):
//
//   eddsa verify-appcast APPCAST ARCHIVE PUBLIC_KEY
//       Checks the appcast's item for ARCHIVE (named by its file name) the way Sparkle will on a person's Mac: its
//       length is the archive's, and its sparkle:edSignature verifies against PUBLIC_KEY, the one the app ships with.
//       A release whose appcast fails this would be refused by every copy of the app, so the recipe stops on it.
//   eddsa verify-feed APPCAST PUBLIC_KEY
//       Checks the appcast's own signature the way Sparkle does when the app requires a signed feed: the block
//       generate_appcast appends (`<!-- sparkle-signatures:`) names the length of what comes before it and its
//       signature, which verifies against PUBLIC_KEY. Every copy refuses a feed that fails this.
//   eddsa throwaway-key FOLDER
//       A key pair for the release check only: FOLDER/private-key holds the private seed, base64, as Sparkle's
//       --ed-key-file reads it, readable by this user alone. Prints the public key. It never touches a Keychain.
//
// Nothing here prints a private key.
import CryptoKit
import Foundation

func fail(_ words: String) -> Never {
    FileHandle.standardError.write(Data((words + "\n").utf8))
    exit(1)
}

func verifyAppcast(appcast: URL, archive: URL, publicKey: String) {
    guard let keyBytes = Data(base64Encoded: publicKey), keyBytes.count == 32,
        let key = try? Curve25519.Signing.PublicKey(rawRepresentation: keyBytes)
    else { fail("verify-appcast: the public key isn't a base64 Ed25519 key of 32 bytes") }
    guard let document = try? XMLDocument(contentsOf: appcast) else { fail("verify-appcast: can't read \(appcast.path)") }
    let enclosures = (try? document.nodes(forXPath: "//item/enclosure")) ?? []
    let name = archive.lastPathComponent
    let matching = enclosures.compactMap { $0 as? XMLElement }.filter {
        URL(string: $0.attribute(forName: "url")?.stringValue ?? "")?.lastPathComponent == name
    }
    guard matching.count == 1, let enclosure = matching.first else {
        fail("verify-appcast: the appcast has \(matching.count) items for \(name), not one")
    }
    guard let bytes = try? Data(contentsOf: archive) else { fail("verify-appcast: can't read \(archive.path)") }
    guard enclosure.attribute(forName: "length")?.stringValue == String(bytes.count) else {
        fail("verify-appcast: the appcast's length for \(name) isn't the archive's \(bytes.count) bytes")
    }
    guard let signature = enclosure.attribute(forName: "sparkle:edSignature")?.stringValue,
        let signatureBytes = Data(base64Encoded: signature)
    else { fail("verify-appcast: the appcast's item for \(name) has no EdDSA signature") }
    guard key.isValidSignature(signatureBytes, for: bytes) else {
        fail("verify-appcast: \(name)'s signature in the appcast doesn't verify against the app's public key")
    }
    print("verified: \(name), \(bytes.count) bytes, signed with the app's update key")
}

func verifyFeed(appcast: URL, publicKey: String) {
    guard let keyBytes = Data(base64Encoded: publicKey), keyBytes.count == 32,
        let key = try? Curve25519.Signing.PublicKey(rawRepresentation: keyBytes)
    else { fail("verify-feed: the public key isn't a base64 Ed25519 key of 32 bytes") }
    guard let bytes = try? Data(contentsOf: appcast) else { fail("verify-feed: can't read \(appcast.path)") }
    // As SPUExtractAppcastContent reads it: the last signing block, and everything before it is what was signed.
    guard let block = bytes.range(of: Data("<!-- sparkle-signatures:\n".utf8), options: .backwards),
        let end = bytes.range(of: Data("-->".utf8), in: block.upperBound..<bytes.endIndex),
        let fields = String(data: bytes[block.upperBound..<end.lowerBound], encoding: .utf8)
    else { fail("verify-feed: the appcast carries no signature of its own") }
    let content = bytes[bytes.startIndex..<block.lowerBound]
    var signature: Data?
    var length: Int?
    for line in fields.split(separator: "\n") {
        if line.hasPrefix("edSignature:") {
            signature = Data(base64Encoded: line.dropFirst("edSignature:".count).trimmingCharacters(in: .whitespaces))
        } else if line.hasPrefix("length:") {
            length = Int(line.dropFirst("length:".count).trimmingCharacters(in: .whitespaces))
        }
    }
    guard let signature else { fail("verify-feed: the appcast's signing block has no edSignature") }
    guard length == content.count else {
        fail("verify-feed: the signing block's length isn't the \(content.count) bytes before it")
    }
    guard key.isValidSignature(signature, for: content) else {
        fail("verify-feed: the appcast's own signature doesn't verify against the app's public key")
    }
    print("verified: the appcast, \(content.count) bytes, signed with the app's update key")
}

func throwawayKey(folder: URL) {
    let key = Curve25519.Signing.PrivateKey()
    let file = folder.appending(path: "private-key")
    let made = FileManager.default.createFile(
        atPath: file.path, contents: Data(key.rawRepresentation.base64EncodedString().utf8),
        attributes: [.posixPermissions: 0o600])
    guard made else { fail("throwaway-key: can't write \(file.path)") }
    print(key.publicKey.rawRepresentation.base64EncodedString())
}

let arguments = CommandLine.arguments.dropFirst()
switch (arguments.first, arguments.count) {
case ("verify-appcast", 4):
    let rest = Array(arguments.dropFirst())
    verifyAppcast(
        appcast: URL(fileURLWithPath: rest[0]), archive: URL(fileURLWithPath: rest[1]), publicKey: rest[2])
case ("verify-feed", 3):
    let rest = Array(arguments.dropFirst())
    verifyFeed(appcast: URL(fileURLWithPath: rest[0]), publicKey: rest[1])
case ("throwaway-key", 2):
    throwawayKey(folder: URL(fileURLWithPath: Array(arguments)[1], isDirectory: true))
default:
    fail(
        "usage: eddsa verify-appcast APPCAST ARCHIVE PUBLIC_KEY | eddsa verify-feed APPCAST PUBLIC_KEY"
            + " | eddsa throwaway-key FOLDER")
}
