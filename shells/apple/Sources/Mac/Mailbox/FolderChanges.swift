#if os(macOS)
import SwiftUI

/// What the folder dialog asks: the name of a new folder, which the mail Move To was moving then goes into; a
/// folder's new name; or whether to delete a folder.
enum FolderAsk: Equatable {
    case create(MoveAsk, name: String)
    case rename(account: String, folder: API.RailFolder)
    case delete(account: String, folder: API.RailFolder)

    var account: String {
        switch self {
        case .create(let move, _): move.account
        case .rename(let account, _), .delete(let account, _): account
        }
    }
}

/// What a change to an account's folders came to: done, with the folder's name as the server lists it now; a folder
/// that still holds mail, which a delete asks about first, with how many, the server's words, and whether its mail
/// goes to Trash (on Gmail it stays in All Mail); or the server's no.
enum FolderChange: Equatable {
    case done(String)
    case holds(Int, String, toTrash: Bool)
    case refused(String)
}

extension Mailbox {
    /// Make a folder named `name` on the account's server, then read its folders again, so the rail and Move To list it.
    func makeFolder(_ name: String, account: String) async -> FolderChange {
        guard let calls else { return .refused(FolderWords.notOpen) }
        do {
            let made = try await calls.post(API.Post.FoldersCreate.self, API.NewFolderBody(account: account, name: name))
            await readCounts()
            return .done(made.folder)
        } catch {
            return FolderWords.refusal(error)
        }
    }

    /// New Folder in Move To: make it, then move what Move To was moving into it.
    func makeFolderAndMove(_ name: String, ask: MoveAsk) async -> FolderChange {
        let made = await makeFolder(name, account: ask.account)
        if case .done(let folder) = made {
            await moveNow(to: folder, label: name.trimmingCharacters(in: .whitespaces))
        }
        return made
    }

    /// The folder a rail's place is, where the rail's menu offers to rename or delete it: one the server says a person
    /// may change (`changeable`), never one that plays a part or that the server keeps, as Gmail's Starred. The server
    /// still has the last word.
    func changeableFolder(_ place: Place) -> (account: String, folder: API.RailFolder)? {
        guard case .folder(let account, let name) = place,
              let folder = folders(of: account).first(where: { $0.name == name }), folder.changeable else { return nil }
        return (account, folder)
    }

    /// Rename one of the account's folders on its server, keeping its parent, then read its folders again. The window
    /// open on it stays on it under its new name.
    func renameFolder(_ folder: API.RailFolder, to name: String, account: String) async -> FolderChange {
        guard let calls else { return .refused(FolderWords.notOpen) }
        do {
            let renamed = try await calls.post(API.Post.FoldersRename.self,
                                               API.RenameFolderBody(account: account, folder: folder.name, name: name))
            if app?.place == .folder(account: account, folder: folder.name) {
                app?.place = .folder(account: account, folder: renamed.folder)
            }
            await readCounts()
            say(String(localized: "Renamed to \(name)"))
            return .done(renamed.folder)
        } catch {
            return FolderWords.refusal(error)
        }
    }

    /// Delete one of the account's folders on its server. A folder that holds mail is deleted only once a person has
    /// said yes (`confirm`), and its mail goes to Trash first (on Gmail it stays in All Mail); the server never
    /// expunges it. The window open on it goes to the account's Inbox. A delete the server stopped may have moved mail
    /// already, so the folders are read again.
    func deleteFolder(_ folder: API.RailFolder, account: String, confirm: Bool) async -> FolderChange {
        guard let calls else { return .refused(FolderWords.notOpen) }
        do {
            let deleted = try await calls.post(API.Post.FoldersDelete.self,
                                               API.DeleteFolderBody(account: account, confirm: confirm ? true : nil,
                                                                    folder: folder.name))
            if app?.place == .folder(account: account, folder: folder.name) {
                let inbox = folders(of: account).first { $0.role == "inbox" }?.name ?? "INBOX"
                app?.place = .folder(account: account, folder: inbox)
            }
            await readCounts()
            say(FolderWords.deleted(folder.ownName, trashed: deleted.trashed))
            return .done(folder.name)
        } catch {
            let said = FolderWords.refusal(error)
            if case .refused = said { await readCounts() }
            return said
        }
    }
}

/// What the folder dialog and its verbs say.
enum FolderWords {
    static let notOpen = String(localized: "MailMind isn’t ready yet. Try again in a moment.")
    static let failed = String(localized: "The server didn’t make that change. Try again in a moment.")

    /// The server's no as the dialog says it: a folder that holds mail says how many, and whether it goes to Trash,
    /// which the delete asks about.
    static func refusal(_ error: any Error) -> FolderChange {
        guard let problem = error as? Problem else { return .refused(failed) }
        if case .number(let count)? = problem.more["messages"], count > 0 {
            let toTrash = problem.more["to_trash"] != .bool(false)
            return .holds(Int(count), problem.description, toTrash: toTrash)
        }
        return .refused(problem.description)
    }

    /// "Deleted Receipts", or with mail moved out first, "Moved 3 to Trash and deleted Receipts".
    static func deleted(_ name: String, trashed: Int) -> String {
        trashed > 0 ? String(localized: "Moved \(trashed) to Trash and deleted \(name)")
            : String(localized: "Deleted \(name)")
    }
}


/// The folder dialog, in Meadow's form as Save View draws it: a new folder's name, typed or as Move To was given it; a
/// folder's new name; or whether to delete one, and, once the server says it holds mail, that the mail goes to Trash
/// first, which a second press says yes to. What the server said if it refused stands under the name.
struct FolderSheet: View {
    let mailbox: Mailbox
    let ask: FolderAsk
    let close: @MainActor () -> Void
    @State private var name = ""
    @State private var working = false
    @State private var refusal: String?
    /// How many messages the folder being deleted holds, once the server has said, and whether they go to Trash.
    @State private var holds: (count: Int, words: String, toTrash: Bool)?
    @FocusState private var naming: Bool
    @Environment(\.meadow) private var meadow

    private var title: String {
        switch ask {
        case .create: String(localized: "New folder")
        case .rename(_, let folder): String(localized: "Rename “\(folder.ownName)”")
        case .delete(_, let folder): String(localized: "Delete “\(folder.ownName)”?")
        }
    }

    private var line: String {
        switch ask {
        case .create: String(localized: "The mail you chose moves into it.")
        case .rename: String(localized: "The folder keeps its place and its mail.")
        case .delete: holds?.words ?? String(localized: "The folder is deleted on the server.")
        }
    }

    private var action: String {
        switch ask {
        case .create: String(localized: "Make and Move")
        case .rename: String(localized: "Rename")
        case .delete: Self.deleteAction(holds.map(\.toTrash))
        }
    }

    private var asksName: Bool {
        if case .delete = ask { return false }
        return true
    }

    var body: some View {
        let empty = asksName && name.trimmingCharacters(in: .whitespaces).isEmpty
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 8) {
                Text(title)
                    .font(.meadow(.display, 18, .bold))
                    .foregroundStyle(meadow.ink)
                    .lineLimit(1)
                    .accessibilityAddTraits(.isHeader)
                Spacer()
                Button(action: close) { MeadowIconView(.x) }
                    .meadowButton(.quiet, icon: true)
                    .accessibilityLabel("Close")
            }
            .padding(.top, 14)
            .padding(.horizontal, 16)
            .padding(.bottom, 10)
            if asksName {
                MeadowFieldRow(label: String(localized: "Name")) {
                    TextField("", text: $name)
                        .textFieldStyle(.plain)
                        .font(.meadow(.ui, 14.5))
                        .foregroundStyle(meadow.ink)
                        .focused($naming)
                        .accessibilityLabel("Name")
                        .onSubmit(run)
                        .onExitCommand { close() }
                }
            }
            Text(refusal ?? line)
                .font(.meadow(.ui, 13, refusal == nil ? .regular : .semibold))
                .foregroundStyle(refusal == nil ? meadow.ink2 : meadow.rust)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.vertical, 10)
                .padding(.horizontal, 18)
                .frame(maxWidth: .infinity, alignment: .leading)
                .overlay(alignment: .top) { Rectangle().fill(meadow.line).frame(height: 1) }
            HStack(spacing: 8) {
                if working { ProgressView().controlSize(.small) }
                Spacer()
                Button("Cancel", action: close)
                    .meadowButton(.quiet)
                    .keyboardShortcut(.cancelAction)
                Button(action, action: run)
                    .meadowButton(.primary, del: !asksName)
                    .disabled(empty || working)
                    .keyboardShortcut(asksName ? nil : .defaultAction)
            }
            .padding(.vertical, 12)
            .padding(.horizontal, 16)
            .overlay(alignment: .top) { Rectangle().fill(meadow.line).frame(height: 1) }
        }
        .onAppear {
            name = Self.startingName(ask)
            naming = asksName
        }
    }

    /// What the name starts as: the name Move To was given, or the folder's own name as a person reads it, its
    /// parents left out and LIST's encoding read (`own_name`), which the server takes back unchanged.
    static func startingName(_ ask: FolderAsk) -> String {
        switch ask {
        case .create(_, let given): given
        case .rename(_, let folder): folder.ownName
        case .delete: ""
        }
    }

    /// What a delete's button says: Delete, and once the server has said the folder holds mail, what a second press
    /// does with it (`toTrash`): moves it to Trash first, or on Gmail leaves it in All Mail.
    static func deleteAction(_ toTrash: Bool?) -> String {
        switch toTrash {
        case nil: String(localized: "Delete")
        case true?: String(localized: "Move to Trash and Delete")
        case false?: String(localized: "Delete Folder")
        }
    }

    private func run() {
        let typed = name.trimmingCharacters(in: .whitespaces)
        guard !working, !asksName || !typed.isEmpty else { return }
        working = true
        refusal = nil
        Task {
            let said: FolderChange
            switch ask {
            case .create(let move, _): said = await mailbox.makeFolderAndMove(typed, ask: move)
            case .rename(let account, let folder): said = await mailbox.renameFolder(folder, to: typed, account: account)
            case .delete(let account, let folder):
                said = await mailbox.deleteFolder(folder, account: account, confirm: holds != nil)
            }
            working = false
            switch said {
            case .done: close()
            case .holds(let count, let words, let toTrash): holds = (count, words, toTrash)
            case .refused(let words): refusal = words
            }
        }
    }
}
#endif
