#if os(macOS)
import AppKit
import QuickLook
import SwiftUI

/// The Document Hub (discovery/Docs.tsx): every attachment across the accounts, searched, narrowed to a kind, in
/// sections by sender or by type or newest first, as the server sorts and pages them. A file opens its message, shows
/// in Quick Look, or is saved where a person says.
struct DocumentHub: View {
    let mailbox: Mailbox
    @Bindable var model: DocumentsModel
    /// The file Quick Look shows: a copy in a folder of the app's own temporary files, gone once it closes.
    @State private var looking: URL?
    @FocusState private var typing: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            TextField("Search documents", text: $model.typed, prompt: Text("Search files, senders, subjects…"))
                .textFieldStyle(.roundedBorder)
                .focused($typing)
                .onChange(of: typing) { _, now in mailbox.typing = now }
            if let first = model.first {
                ScrollView(.horizontal) {
                    HStack(spacing: 6) {
                        Chip(title: "All", count: first.total, on: model.kind == nil) { model.kind = nil }
                        ForEach(API.DocumentKind.allCases, id: \.self) { kind in
                            if let n = first.facets[kind.rawValue], n > 0 {
                                Chip(title: DocumentsModel.label(kind), count: n, on: model.kind == kind) { model.kind = kind }
                            }
                        }
                    }
                }
                .scrollIndicators(.never)
            }
            Picker("Show files", selection: $model.showing) {
                ForEach(DocumentsModel.Showing.allCases) { Text($0.title).tag($0) }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .fixedSize()
            files
        }
        .padding(.horizontal, 12)
        .padding(.top, 4)
        .quickLookPreview($looking)
        .onChange(of: looking) { old, _ in
            if let old { try? FileManager.default.removeItem(at: old.deletingLastPathComponent()) }
        }
    }

    @ViewBuilder private var files: some View {
        if model.failed, model.first == nil {
            ContentUnavailableView {
                Label("Couldn’t load documents.", systemImage: "exclamationmark.triangle")
            } actions: {
                Button("Retry") { model.reload() }
            }
        } else if let first = model.first {
            if model.docs.isEmpty {
                ContentUnavailableView(first.total > 0 ? Words.noFile : Words.noDocuments, systemImage: "doc.on.doc")
            } else {
                List {
                    ForEach(runs, id: \.id) { run in
                        if let heading = run.heading {
                            Section {
                                rows(run.docs)
                            } header: {
                                HStack {
                                    Text(heading)
                                    if let count = run.count { Text("\(count)").foregroundStyle(.secondary) }
                                }
                            }
                        } else {
                            rows(run.docs)
                        }
                    }
                    if model.left > 0 {
                        Button(model.readingMore ? "Loading…" : "Show more · \(model.left) left") { model.more() }
                            .buttonStyle(.link)
                            .disabled(model.readingMore)
                    }
                }
                .listStyle(.inset)
            }
        } else {
            ContentUnavailableView {
                ProgressView().controlSize(.small)
                Text("Gathering your files…")
            }
        }
    }

    private func rows(_ docs: [API.Document]) -> some View {
        ForEach(docs, id: \.self) { doc in
            DocumentRow(doc: doc, open: { open(doc) }, look: { look(at: doc) }, save: { save(doc) })
        }
    }

    /// The files in their sections, in the order the server sent them: a section starts where a file's group changes.
    private var runs: [Run] {
        var runs: [Run] = []
        for doc in model.docs {
            let id = model.showing == .newest ? "" : model.showing == .type ? doc.kind.rawValue : doc.group
            if runs.last?.id == id {
                runs[runs.count - 1].docs.append(doc)
            } else {
                let heading = model.showing == .newest ? nil : model.heading(of: doc)
                let count = model.showing == .sender ? model.section(doc.group)?.count : model.section(doc.kind.rawValue)?.count
                // A list that came back to a section further down shows it under its heading again, apart.
                runs.append(Run(id: runs.contains { $0.id == id } ? "\(id)#\(runs.count)" : id, heading: heading,
                                count: count, docs: [doc]))
            }
        }
        return runs
    }

    private struct Run {
        var id: String
        var heading: String?
        var count: Int?
        var docs: [API.Document]
    }

    private func open(_ doc: API.Document) {
        mailbox.chose([])
        mailbox.app?.open(MailCopy(key: doc.key, folder: doc.folder, uid: doc.uid, account: doc.account))
    }

    private func look(at doc: API.Document) {
        guard let calls = mailbox.calls, let target = doc.preview ?? Optional(doc.url) else { return }
        Task {
            do {
                let file = try await calls.bytes(at: target)
                let folder = FileManager.default.temporaryDirectory
                    .appending(path: "MailMind-Preview", directoryHint: .isDirectory)
                    .appending(path: UUID().uuidString, directoryHint: .isDirectory)
                try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
                let url = folder.appending(path: Self.safeName(doc.filename))
                try file.data.write(to: url, options: .atomic)
                looking = url
            } catch {
                mailbox.say(String(localized: "Couldn’t preview the file"), detail: (error as? Problem)?.detail)
            }
        }
    }

    private func save(_ doc: API.Document) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = Self.safeName(doc.filename)
        panel.canCreateDirectories = true
        guard panel.runModal() == .OK, let url = panel.url, let calls = mailbox.calls else { return }
        Task {
            do {
                try await calls.bytes(at: doc.url).data.write(to: url, options: .atomic)
                mailbox.say("Saved \(url.lastPathComponent)")
            } catch {
                mailbox.say(String(localized: "Couldn’t save the file"), detail: (error as? Problem)?.detail)
            }
        }
    }

    /// A file's name with nothing that would put it in another folder.
    static func safeName(_ name: String) -> String {
        let flat = name.replacingOccurrences(of: "/", with: "-").replacingOccurrences(of: ":", with: "-")
            .trimmingCharacters(in: .whitespacesAndNewlines.union(CharacterSet(charactersIn: ".")))
        return flat.isEmpty ? "file" : flat
    }
}

private struct Chip: View {
    let title: String
    let count: Int
    let on: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 4) {
                Text(title)
                Text("\(count)").foregroundStyle(on ? Color.white.opacity(0.85) : .secondary)
            }
            .font(.callout)
            .padding(.horizontal, 9)
            .padding(.vertical, 3)
            .foregroundStyle(on ? Color.white : .primary)
            .background(on ? Color.accentColor : Color.secondary.opacity(0.12), in: .capsule)
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(on ? .isSelected : [])
    }
}

private struct DocumentRow: View {
    let doc: API.Document
    let open: () -> Void
    let look: () -> Void
    let save: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: DocumentsModel.symbol(doc.kind))
                .font(.title3)
                .foregroundStyle(.secondary)
                .frame(width: 24)
            VStack(alignment: .leading, spacing: 2) {
                Text(doc.filename).fontWeight(.medium).lineLimit(1).truncationMode(.middle)
                Text("\(Text(doc.from).fontWeight(.medium)) · \(doc.subject) · \(doc.when)")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer(minLength: 4)
            Text(Words.size(doc.size)).font(.callout.monospacedDigit()).foregroundStyle(.secondary)
            if doc.preview != nil {
                Button("Preview", systemImage: "eye", action: look)
                    .labelStyle(.iconOnly)
                    .buttonStyle(.borderless)
                    .help("Quick Look")
                    .accessibilityLabel("Preview \(doc.filename)")
            }
            Button("Save", systemImage: "arrow.down.circle", action: save)
                .labelStyle(.iconOnly)
                .buttonStyle(.borderless)
                .help("Save…")
                .accessibilityLabel("Save \(doc.filename)")
        }
        .padding(.vertical, 2)
        .contentShape(Rectangle())
        .onTapGesture(perform: open)
        .contextMenu {
            Button("Open Message", action: open)
            Button("Quick Look", action: look)
            Button("Save…", action: save)
        }
        .accessibilityElement(children: .combine)
        .accessibilityAction(named: "Open message", open)
    }
}

/// A lane in place of the list (discovery/Lanes.tsx): Feed's newsletters as a river of cards, and Paper Trail's
/// receipts as a ledger by month.
struct LaneColumn: View {
    let mailbox: Mailbox
    let model: LaneModel

    var body: some View {
        if !model.loaded {
            ProgressView().controlSize(.small).frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if model.hits.isEmpty, let problem = model.problem {
            ContentUnavailableView {
                Label("Search failed.", systemImage: "exclamationmark.triangle")
            } description: {
                Text(problem)
            } actions: {
                Button("Retry") { model.refresh() }
            }
        } else if model.hits.isEmpty {
            ContentUnavailableView(model.search.lane == .feed ? Words.feed : Words.receipts,
                                   systemImage: model.search.lane == .feed ? "book" : "receipt")
        } else if model.search.lane == .feed {
            FeedRiver(mailbox: mailbox, model: model)
        } else {
            PaperTrail(mailbox: mailbox, model: model)
        }
    }
}

/// What a lane's row opens, and whether it is open.
@MainActor
private func opening(_ hit: API.SearchHit, in mailbox: Mailbox) -> (open: () -> Void, isOpen: Bool) {
    let row = API.FolderRow(hit)
    return ({ mailbox.chose([row]) }, mailbox.app?.selection.copy == MailCopy(row))
}

private struct MoreButton: View {
    let model: LaneModel

    var body: some View {
        if model.hasMore {
            Button("Load more results") { model.more() }
                .buttonStyle(.link)
                .frame(maxWidth: .infinity)
                .padding(8)
        }
    }
}

/// Newsletters as a calm reading river: who, when, which account, the headline, the AI's summary or the snippet,
/// and an Archive on each.
private struct FeedRiver: View {
    let mailbox: Mailbox
    let model: LaneModel

    var body: some View {
        ScrollView {
            LazyVStack(spacing: 10) {
                ForEach(model.hits, id: \.self) { hit in
                    card(hit)
                }
                MoreButton(model: model)
            }
            .padding(12)
        }
    }

    private func card(_ hit: API.SearchHit) -> some View {
        let (open, isOpen) = opening(hit, in: mailbox)
        let subject = hit.subject.isEmpty ? String(localized: "(no subject)") : hit.subject
        let row = API.FolderRow(hit)
        return VStack(alignment: .leading, spacing: 6) {
            Text([row.sender, hit.when, mailbox.name(of: hit.account)].joined(separator: " · "))
                .font(.caption.weight(.medium))
                .foregroundStyle(.secondary)
            Text(subject)
                .font(.headline.weight(hit.seen ? .medium : .bold))
            if !row.gist.isEmpty {
                Text(row.gist).font(.callout).foregroundStyle(.secondary).lineLimit(3)
            }
            HStack {
                Spacer()
                Button("Archive", systemImage: "archivebox") { mailbox.file(.archive, rows: [row]) }
                    .buttonStyle(.borderless)
                    .help("Read & archive")
                    .accessibilityLabel("Archive “\(subject)”")
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(isOpen ? Color.accentColor.opacity(0.14) : Color(nsColor: .controlBackgroundColor), in: .rect(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(.separator))
        .contentShape(Rectangle())
        .onTapGesture(perform: open)
        .accessibilityElement(children: .contain)
        .accessibilityAction(named: "Open", open)
    }
}

/// Receipts as a ledger: each month's items with a subtotal for each currency, refunds taken away.
private struct PaperTrail: View {
    let mailbox: Mailbox
    let model: LaneModel

    var body: some View {
        List {
            ForEach(Ledger.months(model.hits)) { month in
                Section {
                    ForEach(month.lines) { line in
                        lineRow(line)
                    }
                } header: {
                    HStack {
                        Text(Ledger.monthName(month.yearMonth)).fontWeight(.semibold)
                        Spacer()
                        Text(totals(month)).monospacedDigit()
                    }
                }
            }
            MoreButton(model: model)
        }
        .listStyle(.inset)
    }

    private func totals(_ month: Ledger.Month) -> String {
        let items = "\(month.lines.count) item\(month.lines.count == 1 ? "" : "s")"
        guard !month.totals.isEmpty else { return items }
        return items + "  —  " + month.totals.map { Ledger.money($0.currency, $0.value) }.joined(separator: "  ·  ")
    }

    private func lineRow(_ line: Ledger.Line) -> some View {
        let (open, isOpen) = opening(line.hit, in: mailbox)
        let row = API.FolderRow(line.hit)
        return HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 2) {
                Text("\(Text(row.sender).fontWeight(line.hit.seen ? .regular : .semibold)) \(Text("· \(mailbox.name(of: line.hit.account))").font(.caption).foregroundStyle(.secondary))")
                Text((line.refund ? "↩ " : "") + (line.hit.subject.isEmpty ? String(localized: "(no subject)") : line.hit.subject))
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 2) {
                Text(line.amount.map { Ledger.money($0.currency, line.shown ?? $0.value) } ?? "—")
                    .monospacedDigit()
                    .fontWeight(.medium)
                    .foregroundStyle(line.amount == nil ? .secondary : line.refund ? Color.green : .primary)
                Text(line.hit.when).font(.caption).foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 2)
        .listRowBackground(isOpen ? Color.accentColor.opacity(0.14) : nil)
        .contentShape(Rectangle())
        .onTapGesture(perform: open)
        .accessibilityElement(children: .combine)
        .accessibilityAction(named: "Open", open)
    }
}
#endif
