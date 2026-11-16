import Foundation
import XCTest

/// The process opens one core over its data folder: the mail window, closed and opened again from the Dock, shows the
/// one already open, and two windows starting at once open it once.
@MainActor
final class OneCoreTests: XCTestCase {
    /// Guards only this test's empty database.
    private static let key = "6c1e9a3f5b7d0c2e4a6f8b1d3e5c7a9f0b2d4e6a8c1f3b5d7e9a2c4f6b8d0e1a"
    nonisolated(unsafe) private var folder: URL!

    override func setUpWithError() throws {
        folder = FileManager.default.temporaryDirectory.appending(path: "mailmind-one-core-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: folder)
    }

    private var home: CoreHome {
        CoreHome(folder: folder.path(percentEncoded: false),
                 variables: ["MAILMIND_DB_KEY": Self.key, "MAILMIND_NOSYNC": "1"],
                 standIn: folder.appending(path: "keychain.json").path(percentEncoded: false))
    }

    func testEveryMailWindowShowsTheProcesssOneApp() {
        XCTAssertTrue(MailApp.shared === MailApp.shared, "the window takes the process's app, never one of its own")
    }

    func testTwoStartsAtOnceOpenOneCoreAndALaterStartOpensNone() async throws {
        let opened = Opened()
        let home = home
        let app = MailApp(home: { home }, open: { home in
            await opened.add()
            return try await Core.open(home)
        })
        async let first: Void = app.start()
        async let second: Void = app.start()
        _ = await (first, second)
        let once = await opened.count
        XCTAssertEqual(once, 1, "a second window's start leaves the opening to the first")
        XCTAssertEqual(app.stage, .open)
        XCTAssertEqual(app.folder?.lastPathComponent, folder.lastPathComponent, "the folder its log and Start Fresh use")
        // The window closed, and opened again from the Dock.
        await app.start()
        let still = await opened.count
        XCTAssertEqual(still, 1, "a window opened again shows the core already open")
        await app.core?.close()
    }
}

private actor Opened {
    private(set) var count = 0

    func add() {
        count += 1
    }
}
