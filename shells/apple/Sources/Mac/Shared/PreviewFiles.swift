#if os(macOS)
import AppKit
import Foundation

/// The copies Quick Look shows. Quick Look previews only a file, so an attachment is written out for it, decrypted,
/// though the database keeps it encrypted. So each copy goes in a folder of MailMind's own in the user's temporary
/// folder, readable by them alone; it is removed when its preview closes, and every copy is swept at launch and at
/// quit, so one a crash left behind outlives it only until MailMind next opens.
enum PreviewFiles {
    static let folderName = "MailMind-Preview"

    static func folder(in base: URL = FileManager.default.temporaryDirectory) -> URL {
        base.appending(path: folderName, directoryHint: .isDirectory)
    }

    /// Writes `data` as `name` (already made safe to use as a file's name) in a folder of its own, for one preview.
    static func write(_ data: Data, named name: String, in base: URL = FileManager.default.temporaryDirectory) throws
        -> URL
    {
        let files = FileManager.default
        let root = folder(in: base)
        try files.createDirectory(at: root, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        // An existing folder keeps the permissions it was made with: make sure it is still this user's alone.
        try files.setAttributes([.posixPermissions: 0o700], ofItemAtPath: root.path)
        let one = root.appending(path: UUID().uuidString, directoryHint: .isDirectory)
        try files.createDirectory(at: one, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        let url = one.appending(path: name)
        guard files.createFile(atPath: url.path, contents: data, attributes: [.posixPermissions: 0o600]) else {
            throw CocoaError(.fileWriteUnknown)
        }
        return url
    }

    /// Removes one preview's copy with its folder, and nothing outside MailMind's own.
    static func remove(_ url: URL, in base: URL = FileManager.default.temporaryDirectory) {
        let one = url.deletingLastPathComponent().standardizedFileURL
        guard one.deletingLastPathComponent().path == folder(in: base).standardizedFileURL.path else { return }
        try? FileManager.default.removeItem(at: one)
    }

    /// Removes every copy left.
    static func sweep(in base: URL = FileManager.default.temporaryDirectory) {
        try? FileManager.default.removeItem(at: folder(in: base))
    }

    /// At launch, what a crash left; at quit, what a preview still open holds.
    @MainActor static func sweepNowAndAtQuit() {
        sweep()
        NotificationCenter.default.addObserver(
            forName: NSApplication.willTerminateNotification, object: nil, queue: .main
        ) { _ in sweep() }
    }
}
#endif
