import Foundation
import XCTest

/// The acknowledgements window shows what a release put in the app, and says so when a build carries none.
final class AcknowledgementsTests: XCTestCase {
    /// A bundle laid out as the app is, with or without the file the release writes.
    private func bundle(holding text: String?) throws -> Bundle {
        let folder = FileManager.default.temporaryDirectory
            .appending(path: "acknowledgements-\(UUID().uuidString).bundle")
        addTeardownBlock { try? FileManager.default.removeItem(at: folder) }
        let resources = folder.appending(path: "Contents/Resources")
        try FileManager.default.createDirectory(at: resources, withIntermediateDirectories: true)
        let info: [String: Any] = ["CFBundleIdentifier": "test.example.acknowledgements", "CFBundlePackageType": "BNDL"]
        try PropertyListSerialization.data(fromPropertyList: info, format: .xml, options: 0)
            .write(to: folder.appending(path: "Contents/Info.plist"))
        if let text {
            try Data(text.utf8).write(to: resources.appending(path: "Acknowledgements.txt"))
        }
        return try XCTUnwrap(Bundle(url: folder))
    }

    func testAReleaseShowsTheTextItCarries() throws {
        let text = "MailMind's acknowledgements\n\nSparkle, https://sparkle-project.org\n"
        XCTAssertEqual(Acknowledgements.text(in: try bundle(holding: text)), text)
    }

    func testABuildWithoutThemSaysSo() throws {
        XCTAssertNil(Acknowledgements.text(in: try bundle(holding: nil)))
    }
}
