import Foundation
import XCTest

/// Quick Look's copies of attachments: this user's alone, gone when their preview closes, and swept.
final class PreviewFilesTests: XCTestCase {
    private func base() throws -> URL {
        let base = FileManager.default.temporaryDirectory.appending(path: "preview-files-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: base) }
        return base
    }

    private func permissions(_ url: URL) throws -> Int {
        try XCTUnwrap(FileManager.default.attributesOfItem(atPath: url.path)[.posixPermissions] as? Int)
    }

    func testACopyIsThisUsersAloneInAFolderOfItsOwn() throws {
        let base = try base()
        let url = try PreviewFiles.write(Data("fictional".utf8), named: "notes.txt", in: base)
        XCTAssertEqual(try Data(contentsOf: url), Data("fictional".utf8))
        XCTAssertEqual(try permissions(url), 0o600)
        XCTAssertEqual(try permissions(url.deletingLastPathComponent()), 0o700)
        XCTAssertEqual(try permissions(PreviewFiles.folder(in: base)), 0o700)
        let other = try PreviewFiles.write(Data("fictional".utf8), named: "notes.txt", in: base)
        XCTAssertNotEqual(url, other, "two previews of the same name never share a file")
    }

    func testClosingAPreviewRemovesItsCopyAndNothingElse() throws {
        let base = try base()
        let url = try PreviewFiles.write(Data("a".utf8), named: "a.pdf", in: base)
        let kept = try PreviewFiles.write(Data("b".utf8), named: "b.pdf", in: base)
        let outside = base.appending(path: "elsewhere/c.pdf")
        try FileManager.default.createDirectory(
            at: outside.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data("c".utf8).write(to: outside)

        PreviewFiles.remove(url, in: base)
        PreviewFiles.remove(outside, in: base)

        XCTAssertFalse(FileManager.default.fileExists(atPath: url.deletingLastPathComponent().path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: kept.path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: outside.path), "a file outside MailMind's folder stays")
    }

    func testASweepRemovesEveryCopyLeft() throws {
        let base = try base()
        _ = try PreviewFiles.write(Data("a".utf8), named: "a.pdf", in: base)
        _ = try PreviewFiles.write(Data("b".utf8), named: "b.pdf", in: base)
        PreviewFiles.sweep(in: base)
        XCTAssertFalse(FileManager.default.fileExists(atPath: PreviewFiles.folder(in: base).path))
    }
}
