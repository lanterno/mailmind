#if os(macOS)
import SwiftUI

/// A box to type in over a list of things to pick, found by typing (`Entries` in app/Palette.tsx): the command
/// palette (⌘K), and the folder a move goes to. ↑ and ↓ pick, Return runs, Esc closes.
struct Entries: View {
    struct Entry: Identifiable {
        let id: String
        /// The heading it goes under while nothing is typed; "" for none.
        var group = ""
        let label: String
        var hint: String?
        let run: @MainActor () -> Void
    }

    let entries: [Entry]
    let placeholder: String
    let label: String
    @Environment(\.dismiss) private var dismiss
    @State private var query = ""
    @State private var picked = 0
    @FocusState private var typing: Bool

    private var shown: [Entry] {
        Fuzzy.rank(query.trimmingCharacters(in: .whitespaces), entries, label: \.label)
    }

    var body: some View {
        let shown = shown
        let at = min(picked, max(0, shown.count - 1))
        VStack(spacing: 0) {
            TextField(label, text: $query, prompt: Text(placeholder))
                .textFieldStyle(.plain)
                .font(.title3)
                .padding(12)
                .focused($typing)
                .accessibilityLabel(label)
                .onChange(of: query) { picked = 0 }
                .onKeyPress(.downArrow) { step(1, of: shown.count); return .handled }
                .onKeyPress(.upArrow) { step(-1, of: shown.count); return .handled }
                .onSubmit { run(shown.indices.contains(at) ? shown[at] : nil) }
            Divider()
            ScrollViewReader { scroller in
                List {
                    if shown.isEmpty {
                        Text(Words.matches).foregroundStyle(.secondary)
                    }
                    ForEach(Array(shown.enumerated()), id: \.element.id) { index, entry in
                        if query.trimmingCharacters(in: .whitespaces).isEmpty, !entry.group.isEmpty,
                           index == 0 || shown[index - 1].group != entry.group {
                            Text(entry.group)
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(.secondary)
                                .accessibilityAddTraits(.isHeader)
                                .listRowSeparator(.hidden)
                        }
                        EntryRow(entry: entry, picked: index == at)
                            .id(entry.id)
                            .contentShape(Rectangle())
                            .onTapGesture { run(entry) }
                            .onHover { if $0 { picked = index } }
                            .listRowSeparator(.hidden)
                    }
                }
                .listStyle(.plain)
                .scrollContentBackground(.hidden)
                .onChange(of: at) { if shown.indices.contains(at) { scroller.scrollTo(shown[at].id) } }
            }
        }
        .frame(width: 520, height: 420)
        .onAppear { typing = true }
        .onExitCommand { dismiss() }
    }

    private func step(_ by: Int, of count: Int) {
        guard count > 0 else { return }
        picked = (min(picked, count - 1) + by + count) % count
    }

    /// Close first, so a command that opens something gets the focus.
    private func run(_ entry: Entry?) {
        dismiss()
        guard let entry else { return }
        Task { @MainActor in entry.run() }
    }
}

private struct EntryRow: View {
    let entry: Entries.Entry
    let picked: Bool

    var body: some View {
        HStack {
            Text(entry.label).lineLimit(1)
            Spacer()
            if let hint = entry.hint {
                Text(hint)
                    .font(.callout.monospaced())
                    .foregroundStyle(picked ? Color.white.opacity(0.85) : .secondary)
            }
        }
        .padding(.vertical, 3)
        .padding(.horizontal, 6)
        .foregroundStyle(picked ? Color.white : .primary)
        .background(picked ? Color.accentColor : .clear, in: .rect(cornerRadius: 5))
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(picked ? .isSelected : [])
    }
}

/// ⌘K: everything there is to do from here, found by typing (app/Palette.tsx).
struct PaletteSheet: View {
    let mailbox: Mailbox

    var body: some View {
        Entries(entries: mailbox.commands().map {
            Entries.Entry(id: $0.id, group: $0.group.rawValue, label: $0.label, hint: $0.hint, run: $0.run)
        }, placeholder: String(localized: "Type a command or search…"), label: String(localized: "Command"))
    }
}

/// Move what is chosen or open to a folder picked by typing (app/MoveTo.tsx): only the account's own folders that
/// take filing, as the server says of each, and not the one the mail is in.
struct MoveToSheet: View {
    let mailbox: Mailbox
    let ask: MoveAsk

    var body: some View {
        Entries(entries: mailbox.destinations(ask).map { folder in
            Entries.Entry(id: folder.name, label: Words.role(folder)) { mailbox.move(to: folder) }
        }, placeholder: String(localized: "Move to a folder…  (↑↓ Return)"), label: String(localized: "Folder"))
    }
}

/// The name a search is saved under as a view (`saveSearch`): the query's first 40 characters to start with.
struct SaveViewSheet: View {
    let mailbox: Mailbox
    let query: String
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""

    var body: some View {
        Form {
            Text("Save this search as a view")
                .font(.headline)
            Text(query)
                .font(.callout.monospaced())
                .foregroundStyle(.secondary)
                .lineLimit(2)
            TextField("Name", text: $name)
                .onSubmit(save)
        }
        .formStyle(.columns)
        .padding(20)
        .frame(width: 380)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                Button("Cancel") { dismiss() }
            }
            ToolbarItem(placement: .confirmationAction) {
                Button("Save View", action: save)
                    .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .onAppear { name = String(query.prefix(40)) }
    }

    private func save() {
        mailbox.saveView(named: name, query: query)
        dismiss()
    }
}
#endif
