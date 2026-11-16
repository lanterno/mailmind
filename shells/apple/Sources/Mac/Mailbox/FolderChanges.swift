#if os(macOS)
import SwiftUI

/// What the folder dialog asks: the name of a new folder, which the mail Move To was moving then goes into.
enum FolderAsk: Equatable {
    case create(MoveAsk, name: String)

    var account: String {
        switch self {
        case .create(let move, _): move.account
        }
    }
}

/// What a change to an account's folders came to: done, with the folder's name as the server lists it now; or the
/// server's no, in its words.
enum FolderChange: Equatable {
    case done(String)
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
}

/// What the folder dialog and its verbs say.
enum FolderWords {
    static let notOpen = String(localized: "MailMind isn’t ready yet. Try again in a moment.")
    static let failed = String(localized: "The server didn’t make that change. Try again in a moment.")

    /// The server's no as the dialog says it.
    static func refusal(_ error: any Error) -> FolderChange {
        guard let problem = error as? Problem else { return .refused(failed) }
        return .refused(problem.description)
    }
}

/// The folder dialog, in Meadow's form as Save View draws it: a new folder's name, typed or as Move To was given it,
/// and what the server said if it refused.
struct FolderSheet: View {
    let mailbox: Mailbox
    let ask: FolderAsk
    let close: @MainActor () -> Void
    @State private var name = ""
    @State private var working = false
    @State private var refusal: String?
    @FocusState private var naming: Bool
    @Environment(\.meadow) private var meadow

    private var title: String {
        switch ask {
        case .create: String(localized: "New folder")
        }
    }

    private var line: String {
        switch ask {
        case .create: String(localized: "The mail you chose moves into it.")
        }
    }

    private var action: String {
        switch ask {
        case .create: String(localized: "Make and Move")
        }
    }

    var body: some View {
        let empty = name.trimmingCharacters(in: .whitespaces).isEmpty
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
                Button(action, action: run)
                    .meadowButton(.primary)
                    .disabled(empty || working)
            }
            .padding(.vertical, 12)
            .padding(.horizontal, 16)
            .overlay(alignment: .top) { Rectangle().fill(meadow.line).frame(height: 1) }
        }
        .onAppear {
            if case .create(_, let given) = ask { name = given }
            naming = true
        }
    }

    private func run() {
        let typed = name.trimmingCharacters(in: .whitespaces)
        guard !working, !typed.isEmpty else { return }
        working = true
        refusal = nil
        Task {
            let said: FolderChange
            switch ask {
            case .create(let move, _): said = await mailbox.makeFolderAndMove(typed, ask: move)
            }
            working = false
            switch said {
            case .done: close()
            case .refused(let words): refusal = words
            }
        }
    }
}
#endif
