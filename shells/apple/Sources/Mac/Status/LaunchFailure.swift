#if os(macOS)
import AppKit
import SwiftUI

/// The window when MailMind didn't open: what the core said, in words a person can select and copy, that the mail on
/// the servers is untouched, and the ways on: copy the details for whoever gave you MailMind, see the data folder in
/// the Finder, open again once the step the words name is taken, or, where a new copy fixes it, start fresh with one,
/// the old one kept in `backups/`.
struct LaunchFailure: View {
    let why: String
    /// The data folder, when one was chosen: what Show in Finder shows and Start Fresh empties.
    let folder: URL?
    /// Open again once the database has moved aside.
    let startAgain: @MainActor () async -> Void
    @State private var asking = false
    @State private var trying = false
    @State private var problem: String?
    @Environment(\.meadow) private var meadow

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 10) {
                MeadowIconView(.alert, size: 20).foregroundStyle(meadow.rust)
                Text("MailMind didn’t open")
                    .font(.meadow(.display, 26, .bold))
                    .foregroundStyle(meadow.ink)
                    .accessibilityAddTraits(.isHeader)
            }
            Text(why)
                .font(.meadow(.ui, 15))
                .foregroundStyle(meadow.ink)
                .lineSpacing(MeadowFace.ui.lineSpacing(size: 15, lineHeight: 1.5))
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
                .padding(.horizontal, 14)
                .padding(.vertical, 12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(meadow.paperRaised, in: RoundedRectangle(cornerRadius: 12))
                .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(meadow.line))
                .padding(.top, 16)
            Text(Self.safe(startsFresh: Self.startsFresh(why)))
                .font(.meadow(.ui, 14))
                .foregroundStyle(meadow.ink2)
                .lineSpacing(MeadowFace.ui.lineSpacing(size: 14, lineHeight: 1.5))
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 14)
            HStack(spacing: 8) {
                if Self.triesAgain(why) {
                    Button("Try Again") { Task { await tryAgain() } }
                        .meadowButton(.primary)
                        .disabled(trying)
                }
                Button("Copy Details") { Task { await Diagnostics.copy() } }
                    .meadowButton(Self.triesAgain(why) ? .standard : .primary)
                Button("Show in Finder") {
                    if let folder { AppSeams.links.reveal([folder]) }
                }
                .meadowButton()
                .disabled(folder == nil)
                Spacer(minLength: 8)
                if Self.startsFresh(why) {
                    Button("Start Fresh…") { asking = true }
                        .meadowButton(.danger)
                        .disabled(folder == nil)
                }
            }
            .padding(.top, 22)
            if let problem {
                Text(problem)
                    .font(.meadow(.ui, 13, .semibold))
                    .foregroundStyle(meadow.rust)
                    .padding(.top, 10)
            }
        }
        .padding(28)
        .frame(maxWidth: 560)
        .background(meadow.paper, in: RoundedRectangle(cornerRadius: 18))
        .overlay(RoundedRectangle(cornerRadius: 18).strokeBorder(meadow.line))
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background {
            ZStack {
                meadow.paper2
                MeadowGrain()
            }
            .ignoresSafeArea()
        }
        .confirmationDialog(String(localized: "Start fresh?"), isPresented: $asking) {
            Button("Start Fresh", role: .destructive) { Task { await startFresh() } }
        } message: {
            Text(Self.fresh)
        }
    }

    /// That the mail on the servers is untouched, and what else there is to do: start fresh, where a new copy fixes
    /// what went wrong, or else the step the core's words name, with the details for whoever gave you MailMind. Where
    /// no new copy is offered, the words name no copy on this Mac: the core may have said none was made.
    static func safe(startsFresh: Bool) -> String {
        startsFresh ? String(localized: """
            Your mail on its servers is untouched: this is about the copy MailMind keeps on this Mac. Copy the details \
            and send them to whoever gave you MailMind, or start fresh with a new copy.
            """)
            : String(localized: """
            Your mail on its servers is untouched, and so is anything MailMind keeps on this Mac. Do what the words \
            above say; if that doesn’t help, copy the details and send them to whoever gave you MailMind.
            """)
    }

    /// Whether a new copy of the mail fixes what the core said went wrong, so Start Fresh is offered. A Keychain that
    /// is locked or wouldn't give or keep MailMind's key, and a copy made by a newer MailMind, are not fixed by one:
    /// the core's words for them say what to do instead (unlock the Keychain, install the newest MailMind), and
    /// moving the copy aside would only leave behind what it holds.
    static func startsFresh(_ why: String) -> Bool {
        !why.contains("Unlock your login Keychain") && !why.contains("made by a newer MailMind")
    }

    /// Whether opening again may work once the step the words name is taken: the Keychain unlocked, room made on the
    /// disk, an upgrade tried again, or something MailMind didn't expect.
    static func triesAgain(_ why: String) -> Bool {
        why.localizedCaseInsensitiveContains("open MailMind again")
    }

    static let fresh = String(localized: """
        MailMind moves this Mac’s copy of your mail into the backups folder and makes a new one, then reads your mail \
        from its servers again. Your accounts stay set up. What hadn’t reached a server yet (mail waiting to send, \
        drafts, moves) stays in the old copy.
        """)

    private func tryAgain() async {
        trying = true
        await startAgain()
        trying = false
    }

    private func startFresh() async {
        guard let folder else { return }
        do {
            _ = try StartFresh.move(in: folder)
            problem = nil
            await startAgain()
        } catch {
            problem = String(localized: "The copy couldn’t be moved aside. Show it in the Finder, and move mailmind.db yourself.")
        }
    }
}

/// Start Fresh: the database moved, with its journal, into the `backups` folder beside it, under a name of its own
/// that the core's upgrade backups (`mailmind-v…`, the newest three kept) never prune. The next open makes a new one.
enum StartFresh {
    /// The database and the files SQLite keeps beside it.
    static let files = ["mailmind.db", "mailmind.db-wal", "mailmind.db-shm"]
    static let backups = "backups"

    /// Move the database out of `folder` into its `backups`, named for `now`: where it went, or nil when there was none.
    @discardableResult
    static func move(in folder: URL, now: Date = AppSeams.clock.now, manager: FileManager = .default) throws -> URL? {
        let database = folder.appending(path: files[0])
        guard manager.fileExists(atPath: database.path(percentEncoded: false)) else { return nil }
        let into = folder.appending(path: backups, directoryHint: .isDirectory)
        try manager.createDirectory(at: into, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let stem = stem(in: into, now: now, manager: manager)
        for name in files {
            let file = folder.appending(path: name)
            guard manager.fileExists(atPath: file.path(percentEncoded: false)) else { continue }
            let suffix = String(name.dropFirst("mailmind".count))
            try manager.moveItem(at: file, to: into.appending(path: stem + suffix))
        }
        return into.appending(path: stem + ".db")
    }

    /// `mailmind-before-starting-fresh-<UTC time>`, with a number after it when one of that name is there.
    private static func stem(in folder: URL, now: Date, manager: FileManager) -> String {
        let format = DateFormatter()
        format.locale = Locale(identifier: "en_US_POSIX")
        format.timeZone = TimeZone(identifier: "UTC")
        format.dateFormat = "yyyyMMdd'T'HHmmss'Z'"
        let base = "mailmind-before-starting-fresh-\(format.string(from: now))"
        var stem = base
        var n = 1
        while manager.fileExists(atPath: folder.appending(path: stem + ".db").path(percentEncoded: false)) {
            n += 1
            stem = "\(base)-\(n)"
        }
        return stem
    }
}
#endif
