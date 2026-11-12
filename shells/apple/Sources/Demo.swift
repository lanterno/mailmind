import Foundation
import Observation

/// Step 0's only mode: a fictional mailbox of 100,000 messages, made on first launch in Application Support.
/// Every address in it is at example.test, and its key guards only fictional mail.
@MainActor
@Observable
final class Demo {
    enum Stage: Equatable {
        case starting
        case makingMailbox
        case open
        case failed(String)
    }

    nonisolated static let messages: UInt32 = 100_000

    private(set) var stage = Stage.starting
    let list = ListModel()
    @ObservationIgnored private var session: Session?
    @ObservationIgnored private var view: ListView?

    func start() async {
        guard stage == .starting else { return }
        do {
            let path = try Self.mailboxPath()
            if !FileManager.default.fileExists(atPath: path.path) {
                stage = .makingMailbox
                try await Task.detached(priority: .userInitiated) { try Self.makeMailbox(at: path) }.value
            }
            // Opening reads the disk and orders the whole inbox: never on the main thread.
            let relay = Relay(list)
            let (session, view) = try await Task.detached(priority: .userInitiated) {
                let session = try Session.open(database: path.path, key: fixtureKey(), workers: 1)
                return (session, try session.openList(query: fixtureInbox(), observer: relay))
            }.value
            self.session = session
            self.view = view
            list.attach(view)
            stage = .open
        } catch {
            stage = .failed(String(describing: error))
        }
    }

    nonisolated private static func mailboxPath() throws -> URL {
        let support = try FileManager.default.url(
            for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
        let folder = support
            .appending(path: Bundle.main.bundleIdentifier ?? "app.mailmind.MailMind")
            .appending(path: "Demo")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        return folder.appending(path: "inbox-\(messages).db")
    }

    /// The core refuses a path where something already is, so a mailbox is made beside its final name and
    /// moved there whole: a launch cut short leaves nothing half-made in its place.
    nonisolated private static func makeMailbox(at path: URL) throws {
        let partial = path.appendingPathExtension("partial")
        try? FileManager.default.removeItem(at: partial)
        try buildFixture(path: partial.path, count: messages)
        try FileManager.default.moveItem(at: partial, to: path)
    }
}
