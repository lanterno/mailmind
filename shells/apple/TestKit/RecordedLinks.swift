import Foundation

/// The links, files and folders the app asked to open, kept rather than opened, for the app's links seam
/// (`AppSeams.links`): a test or the driver reads them, and the Mac's browser, Calendar and Finder never move.
@MainActor
final class RecordedLinks {
    /// What was asked to open, in order.
    private(set) var opened: [URL] = []
    /// What was asked to be shown in Finder, each ask's files together.
    private(set) var revealed: [[URL]] = []

    init() {}

    func open(_ url: URL) {
        opened.append(url)
    }

    func reveal(_ files: [URL]) {
        revealed.append(files)
    }
}
