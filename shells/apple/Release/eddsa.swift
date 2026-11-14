// Ed25519 for the release, on CryptoKit (docs/releasing.md):
//
//   eddsa verify-appcast APPCAST ARCHIVE PUBLIC_KEY
//       Checks the appcast's item for ARCHIVE (named by its file name) the way Sparkle will on a person's Mac: its
//       length is the archive's, and its sparkle:edSignature verifies against PUBLIC_KEY, the one the app ships with.
//       A release whose appcast fails this would be refused by every copy of the app, so the recipe stops on it.
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

let arguments = CommandLine.arguments.dropFirst()
switch (arguments.first, arguments.count) {
case ("verify-appcast", 4):
    let rest = Array(arguments.dropFirst())
    verifyAppcast(
        appcast: URL(fileURLWithPath: rest[0]), archive: URL(fileURLWithPath: rest[1]), publicKey: rest[2])
default:
    fail("usage: eddsa verify-appcast APPCAST ARCHIVE PUBLIC_KEY")
}
