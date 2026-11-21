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
    @Environment(\.meadow) private var meadow

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                MeadowIconView(.search, size: 16).foregroundStyle(meadow.ink3)
                TextField("Search documents", text: $model.typed, prompt: Text("Search files, senders, subjects…"))
                    .focused($typing)
            }
            .deskField(focused: typing)
            if let first = model.first {
                ScrollView(.horizontal) {
                    HStack(spacing: 6) {
                        HubPill(title: String(localized: "All"), count: first.total, on: model.kind == nil) {
                            model.kind = nil
                        }
                        ForEach(API.DocumentKind.allCases, id: \.self) { kind in
                            if let n = first.facets[kind.rawValue], n > 0 {
                                HubPill(title: DocumentsModel.label(kind), count: n, on: model.kind == kind) {
                                    model.kind = kind
                                }
                            }
                        }
                    }
                    .padding(3)
                }
                .scrollIndicators(.never)
            }
            MeadowSegmentedChoice(label: String(localized: "Show files"), selection: $model.showing,
                                  options: DocumentsModel.Showing.allCases.map { ($0, $0.title) }, fills: false)
            files
        }
        .padding(.horizontal, 10)
        .padding(.top, 6)
        .quickLookClosable($looking)
        .onChange(of: looking) { old, _ in
            if let old { PreviewFiles.remove(old) }
        }
    }

    @ViewBuilder private var files: some View {
        if model.failed, model.first == nil {
            DeskEmpty(icon: .alert, title: String(localized: "Couldn’t load documents.")) {
                Button("Retry") { model.reload() }.meadowButton(small: true)
            }
            Spacer(minLength: 0)
        } else if let first = model.first {
            if model.docs.isEmpty {
                DeskEmpty(icon: .file, title: first.total > 0 ? Words.noFile : Words.noDocuments)
                Spacer(minLength: 0)
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 0) {
                        ForEach(runs, id: \.id) { run in
                            if let heading = run.heading {
                                HStack(spacing: 6) {
                                    MeadowSectionLabel(heading)
                                    if let count = run.count {
                                        Text("\(count)").font(.meadow(.ui, 11, .heavy)).foregroundStyle(meadow.ink3)
                                    }
                                }
                                .padding(.horizontal, 10)
                                .padding(.top, 14)
                                .padding(.bottom, 6)
                            }
                            rows(run.docs)
                        }
                        if model.left > 0 {
                            Button(model.readingMore ? "Loading…" : "Show more · \(model.left) left") { model.more() }
                                .meadowButton(.quiet, small: true)
                                .disabled(model.readingMore)
                                .frame(maxWidth: .infinity)
                                .padding(8)
                        }
                    }
                    .padding(.bottom, 10)
                }
                .scrollContentBackground(.hidden)
            }
        } else {
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                Text("Gathering your files…").font(.meadow(.ui, 13.5)).foregroundStyle(meadow.ink3)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 28)
            Spacer(minLength: 0)
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
        mailbox.open(MailCopy(key: doc.key, folder: doc.folder, uid: doc.uid, account: doc.account))
    }

    private func look(at doc: API.Document) {
        guard let calls = mailbox.calls, let target = doc.preview ?? Optional(doc.url) else { return }
        Task {
            do {
                let file = try await calls.bytes(at: target)
                looking = try PreviewFiles.write(file.data, named: Self.safeName(doc.filename))
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

/// A kind of file to narrow to: a pill, indigo while chosen, with how many there are.
private struct HubPill: View {
    let title: String
    let count: Int
    let on: Bool
    let action: () -> Void
    @Environment(\.meadow) private var meadow
    @FocusState private var focused: Bool

    var body: some View {
        Button(action: action) {
            HStack(spacing: 5) {
                Text(title)
                Text("\(count)").monospacedDigit().opacity(0.75)
            }
            .font(.meadow(.ui, 12.5, .bold))
            .foregroundStyle(on ? meadow.onPrimary : meadow.ink2)
            .padding(.horizontal, 11)
            .frame(minHeight: 28)
            .background(Capsule().fill(on ? meadow.primary : meadow.paper3))
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .focused($focused)
        .focusEffectDisabled()
        .meadowFocusRing(focused, radius: 14)
        .accessibilityAddTraits(on ? .isSelected : [])
    }
}

private struct DocumentRow: View {
    let doc: API.Document
    let open: () -> Void
    let look: () -> Void
    let save: () -> Void
    @Environment(\.meadow) private var meadow
    @State private var hovering = false

    var body: some View {
        HStack(spacing: 11) {
            MeadowIconView(DocumentsModel.icon(doc.kind), size: 18)
                .foregroundStyle(meadow.ink2)
                .frame(width: 34, height: 34)
                .background(RoundedRectangle(cornerRadius: 10).fill(meadow.paper3))
            VStack(alignment: .leading, spacing: 2) {
                Text(doc.filename)
                    .font(.meadow(.ui, 14, .bold))
                    .foregroundStyle(meadow.ink)
                    .lineLimit(1)
                    .truncationMode(.middle)
                Text("\(Text(doc.from).fontWeight(.semibold)) · \(doc.subject) · \(doc.when)")
                    .font(.meadow(.ui, 12.5))
                    .foregroundStyle(meadow.ink3)
                    .lineLimit(1)
            }
            Spacer(minLength: 4)
            Text(Words.size(doc.size))
                .font(.meadow(.ui, 12.5))
                .monospacedDigit()
                .foregroundStyle(meadow.ink3)
            if doc.preview != nil {
                Button(action: look) { MeadowIconView(.eye, size: 15) }
                    .meadowButton(.quiet, small: true, icon: true)
                    .help("Quick Look")
                    .accessibilityLabel("Preview \(doc.filename)")
            }
            Button(action: save) { MeadowIconView(.download, size: 15) }
                .meadowButton(.quiet, small: true, icon: true)
                .help("Save…")
                .accessibilityLabel("Save \(doc.filename)")
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .background(RoundedRectangle(cornerRadius: 12).fill(hovering ? meadow.paper2 : .clear))
        .contentShape(Rectangle())
        .onHover { hovering = $0 }
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

/// A lane in place of the list (discovery/Lanes.tsx): Feed's newsletters as a river of rows, and Paper Trail's
/// receipts as a ledger by month.
struct LaneColumn: View {
    let mailbox: Mailbox
    let model: LaneModel

    var body: some View {
        if !model.loaded {
            ProgressView().controlSize(.small).frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if model.hits.isEmpty, let problem = model.problem {
            VStack {
                DeskEmpty(icon: .alert, title: String(localized: "Search failed."), words: problem) {
                    Button("Retry") { model.refresh() }.meadowButton(small: true)
                }
                Spacer(minLength: 0)
            }
        } else if model.hits.isEmpty {
            VStack {
                DeskEmpty(icon: model.search.lane == .feed ? .book : .receipt,
                          title: Words.emptyLane(model.search.lane, emptied: model.emptied))
                Spacer(minLength: 0)
            }
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
    var title = String(localized: "Load more results")

    var body: some View {
        if model.hasMore {
            Button(title) { model.more() }
                .meadowButton(.quiet, small: true)
                .frame(maxWidth: .infinity)
                .padding(8)
        }
    }
}

/// Newsletters as a calm reading river, each a row as the list draws one (`.row`): who, which account and when, the
/// headline, the AI's summary in moss or the snippet, and an Archive on each.
private struct FeedRiver: View {
    let mailbox: Mailbox
    let model: LaneModel

    var body: some View {
        ScrollView {
            LazyVStack(spacing: 2) {
                ForEach(model.hits, id: \.self) { hit in
                    FeedRow(mailbox: mailbox, hit: hit)
                }
                MoreButton(model: model)
            }
            .padding(6)
        }
        .scrollContentBackground(.hidden)
    }
}

private struct FeedRow: View {
    let mailbox: Mailbox
    let hit: API.SearchHit
    @Environment(\.meadow) private var meadow
    @State private var hovering = false

    var body: some View {
        let (open, isOpen) = opening(hit, in: mailbox)
        let subject = hit.subject.isEmpty ? String(localized: "(no subject)") : hit.subject
        let row = API.FolderRow(hit)
        let who = RowWords.who(row, mine: RowWords.mine(mailbox.accounts))
        let multi = mailbox.accounts.count > 1
        let account = meadow.account(of: hit.account, among: mailbox.accounts)
        HStack(alignment: .top, spacing: 11) {
            MeadowAvatar(name: who.avatarKey, account: multi ? account : nil)
            VStack(alignment: .leading, spacing: 0) {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(who.name)
                        .font(.meadow(.ui, 14, hit.seen ? .semibold : .heavy))
                        .foregroundStyle(meadow.ink)
                        .lineLimit(1)
                    Spacer(minLength: 8)
                    Text(hit.when)
                        .font(.meadow(.ui, 12.5))
                        .monospacedDigit()
                        .foregroundStyle(meadow.ink3)
                        .lineLimit(1)
                }
                Text(subject)
                    .font(.meadow(.ui, 14, hit.seen ? .regular : .heavy))
                    .foregroundStyle(meadow.ink)
                    .lineLimit(1)
                    .padding(.top, 2)
                if !row.summary.isEmpty {
                    Text(row.summary)
                        .font(.meadow(.ui, 13.5, .medium))
                        .foregroundStyle(meadow.moss)
                        .lineSpacing(MeadowFace.ui.lineSpacing(size: 13.5, lineHeight: 1.45, weight: .medium))
                        .lineLimit(2)
                        .padding(.top, 4)
                        .accessibilityLabel(Text("MailMind summary: \(row.summary)"))
                } else if !row.snippet.isEmpty {
                    Text(row.snippet)
                        .font(.meadow(.ui, 13))
                        .foregroundStyle(meadow.ink3)
                        .lineLimit(2)
                        .padding(.top, 3)
                }
                if multi {
                    MeadowChip(mailbox.name(of: hit.account), tone: .account(account))
                        .padding(.top, 6)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.horizontal, 10)
        .padding(.top, 10)
        .padding(.bottom, 11)
        .background(RoundedRectangle(cornerRadius: 12)
            .fill(isOpen ? meadow.primaryWash : hovering ? meadow.paper2 : .clear))
        .overlay(alignment: .bottomTrailing) {
            if hovering {
                Button { mailbox.file(.archive, rows: [row]) } label: { MeadowIconView(.archive, size: 15) }
                    .meadowButton(.quiet, small: true, icon: true)
                    .help("Read & archive")
                    .accessibilityLabel("Archive “\(subject)”")
                    .padding(6)
            }
        }
        .contentShape(Rectangle())
        .onHover { hovering = $0 }
        .onTapGesture(perform: open)
        .accessibilityElement(children: .contain)
        .accessibilityAction(named: "Open", open)
        .accessibilityAction(named: "Archive") { mailbox.file(.archive, rows: [row]) }
    }
}

// MARK: The paper trail

/// Receipts as Meadow's paper trail (`renderLedger()`). Across the window it is a page: its head with the month
/// chosen, that month's ledger in a table on a card, and beside it the month's total against the month before, each
/// account's share and where the receipts are filed. Beside an open receipt, in the list's room, the same month is a
/// list under its total. Amounts are read from the mail; nothing is asked of the network but the search.
private struct PaperTrail: View {
    let mailbox: Mailbox
    let model: LaneModel
    @State private var chosen: String?
    @State private var width: CGFloat = 1200
    @Environment(\.meadow) private var meadow

    /// Across the window, as the frame lays the paper trail out while no receipt is open.
    private var page: Bool {
        mailbox.app?.place?.spansTheWindow == true && mailbox.app?.selection.copy == nil
    }

    var body: some View {
        let months = Ledger.months(model.hits)
        let index = months.firstIndex { $0.yearMonth == chosen } ?? 0
        ScrollView {
            if months.indices.contains(index) {
                if page {
                    pageView(months, index)
                } else {
                    column(months, index)
                }
            }
        }
        .scrollContentBackground(.hidden)
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { width = $0 }
    }

    // MARK: Across the window

    private func pageView(_ months: [Ledger.Month], _ index: Int) -> some View {
        let month = months[index]
        return VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .bottom, spacing: 12) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Paper trail")
                        .font(.meadow(.display, 28, .bold))
                        .foregroundStyle(meadow.ink)
                        .accessibilityAddTraits(.isHeader)
                    Text("Every receipt, invoice and refund — amounts read from the mail, the mail kept as proof.")
                        .font(.meadow(.ui, 14))
                        .foregroundStyle(meadow.ink2)
                }
                Spacer(minLength: 12)
                MonthButton(months: months, chosen: $chosen, at: index)
            }
            .padding(.bottom, 18)
            if width >= 980 {
                HStack(alignment: .top, spacing: 28) {
                    ledger(month).frame(maxWidth: .infinity)
                    side(months, index).frame(width: 320)
                }
            } else {
                VStack(alignment: .leading, spacing: 24) {
                    ledger(month)
                    side(months, index)
                }
            }
        }
        .padding(.horizontal, 32)
        .padding(.top, 28)
        .padding(.bottom, 56)
        .frame(maxWidth: 1360)
        .frame(maxWidth: .infinity)
    }

    private func ledger(_ month: Ledger.Month) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(spacing: 0) {
                LedgerHead()
                ForEach(month.lines) { line in
                    LedgerRow(mailbox: mailbox, line: line)
                }
            }
            .padding(.horizontal, 6)
            .padding(.vertical, 4)
            .background(RoundedRectangle(cornerRadius: 14).fill(meadow.paperRaised))
            .overlay { RoundedRectangle(cornerRadius: 14).strokeBorder(meadow.line) }
            Text("Amounts are read from each message by MailMind. Open a row to see the original receipt.")
                .font(.meadow(.ui, 12.5))
                .foregroundStyle(meadow.ink3)
                .padding(.top, 14)
            MoreButton(model: model, title: String(localized: "Load older receipts"))
        }
    }

    @ViewBuilder private func side(_ months: [Ledger.Month], _ index: Int) -> some View {
        let month = months[index]
        VStack(alignment: .leading, spacing: 14) {
            DeskSideCard(title: Ledger.monthOnly(month.yearMonth), icon: .receipt) {
                total(months, index, size: 28)
            }
            let accounts = Ledger.byAccount(month)
            if !accounts.isEmpty {
                DeskSideCard(title: String(localized: "By account"), icon: .user) {
                    VStack(spacing: 0) {
                        ForEach(Array(accounts.enumerated()), id: \.offset) { at, share in
                            let value = Ledger.money(share.currency, share.value)
                            MeadowFact(value: value, last: at == accounts.count - 1) {
                                MeadowChip(mailbox.name(of: share.account),
                                           tone: .account(meadow.account(of: share.account, among: mailbox.accounts)))
                            }
                        }
                    }
                }
            }
            let folders = Ledger.byFolder(month)
            DeskSideCard(title: String(localized: "Filed under"), icon: .folder) {
                VStack(spacing: 0) {
                    ForEach(Array(folders.enumerated()), id: \.offset) { at, folder in
                        MeadowFact(PaperTrail.folderName(folder.folder), value: "\(folder.count)",
                                   last: at == folders.count - 1)
                    }
                }
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel(Text("\(Ledger.monthName(month.yearMonth)) totals"))
    }

    /// A month's total and what it says (`.money`, `.money-sub`), or how many lines it has when none had an amount.
    @ViewBuilder private func total(_ months: [Ledger.Month], _ index: Int, size: CGFloat) -> some View {
        if let summary = Ledger.summary(months, at: index) {
            Text(Ledger.money(summary.currency, summary.total))
                .font(.meadow(.display, size, .bold))
                .monospacedDigit()
                .foregroundStyle(meadow.ink)
            Text(Ledger.words(summary))
                .font(.meadow(.ui, 12.5))
                .foregroundStyle(meadow.ink3)
                .padding(.top, -2)
                .fixedSize(horizontal: false, vertical: true)
        } else {
            let count = months[index].lines.count
            Text(count == 1 ? String(localized: "1 message · no amount read")
                 : String(localized: "\(count) messages · no amount read"))
                .font(.meadow(.ui, 12.5))
                .foregroundStyle(meadow.ink3)
        }
    }

    // MARK: In the list's room

    private func column(_ months: [Ledger.Month], _ index: Int) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    total(months, index, size: 22)
                }
                Spacer(minLength: 8)
                MonthButton(months: months, chosen: $chosen, at: index, small: true)
            }
            .padding(.horizontal, 10)
            .padding(.top, 8)
            VStack(spacing: 2) {
                ForEach(months[index].lines) { line in
                    LedgerLine(mailbox: mailbox, line: line)
                }
            }
            MoreButton(model: model, title: String(localized: "Load older receipts"))
        }
        .padding(6)
    }

    /// A folder as the facts name it: the Inbox by its name, a folder of a person's by its last part.
    static func folderName(_ folder: String) -> String {
        if folder.uppercased() == "INBOX" { return String(localized: "Inbox") }
        return folder.split(whereSeparator: { $0 == "/" }).last.map(String.init) ?? folder
    }
}

/// The month the paper trail shows (`.btn` with the calendar): a menu of the months the receipts read so far reach.
private struct MonthButton: View {
    let months: [Ledger.Month]
    @Binding var chosen: String?
    let at: Int
    var small = false
    @State private var asking = false
    @Environment(\.meadow) private var meadow

    var body: some View {
        Button { asking.toggle() } label: {
            MeadowIconView(.cal, size: 15)
            Text(Ledger.monthOnly(months[at].yearMonth))
            MeadowIconView(.down, size: 13)
        }
        .meadowButton(small: small, pressed: asking)
        .help("Choose a month")
        .accessibilityLabel("Month: \(Ledger.monthName(months[at].yearMonth))")
        .popover(isPresented: $asking, arrowEdge: .bottom) {
            VStack(alignment: .leading, spacing: 2) {
                ForEach(Array(months.enumerated()), id: \.offset) { index, month in
                    Button {
                        chosen = month.yearMonth
                        asking = false
                    } label: {
                        HStack(spacing: 16) {
                            Text(Ledger.monthName(month.yearMonth))
                                .font(.meadow(.ui, 13.5, index == at ? .bold : .regular))
                            Spacer(minLength: 0)
                            Text(month.totals.map { Ledger.money($0.currency, $0.value) }.joined(separator: " · "))
                                .font(.meadow(.ui, 12.5))
                                .monospacedDigit()
                                .foregroundStyle(meadow.ink3)
                        }
                        .frame(minWidth: 220)
                    }
                    .meadowButton(.quiet, small: true, pressed: index == at)
                }
            }
            .padding(6)
            .background(meadow.paperRaised)
        }
    }
}

/// The ledger's head (`.ledger th`): small capitals over each column, a firmer line under them.
private struct LedgerHead: View {
    @Environment(\.meadow) private var meadow

    var body: some View {
        LedgerColumns {
            MeadowSectionLabel(String(localized: "Date"))
        } from: {
            MeadowSectionLabel(String(localized: "From"))
        } account: {
            MeadowSectionLabel(String(localized: "Account"))
        } amount: {
            MeadowSectionLabel(String(localized: "Amount"))
        }
        .padding(.vertical, 10)
        .overlay(alignment: .bottom) { Rectangle().fill(meadow.line2).frame(height: 1) }
    }
}

/// The ledger's columns, as wide as the mockup's table sets them: the date, who and what, the account, the amount.
private struct LedgerColumns<When: View, Who: View, Account: View, Amount: View>: View {
    @ViewBuilder let date: When
    @ViewBuilder let from: Who
    @ViewBuilder let account: Account
    @ViewBuilder let amount: Amount

    var body: some View {
        HStack(spacing: 0) {
            date.frame(width: 130, alignment: .leading).padding(.horizontal, 12)
            from.frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, 12)
            account.frame(width: 150, alignment: .leading).padding(.horizontal, 12)
            amount.frame(width: 110, alignment: .trailing).padding(.horizontal, 12)
        }
    }
}

/// A receipt in the ledger (`.ledger td`): when, who and what for, the account's chip, and the amount, a refund's in
/// moss. It opens the receipt.
private struct LedgerRow: View {
    let mailbox: Mailbox
    let line: Ledger.Line
    @Environment(\.meadow) private var meadow
    @FocusState private var focused: Bool
    @State private var hovering = false

    var body: some View {
        let (open, isOpen) = opening(line.hit, in: mailbox)
        let row = API.FolderRow(line.hit)
        Button(action: open) {
            LedgerColumns {
                Text(LedgerDay.short(line.hit.date) ?? line.hit.when)
                    .font(.meadow(.ui, 14))
                    .monospacedDigit()
                    .foregroundStyle(meadow.ink)
            } from: {
                VStack(alignment: .leading, spacing: 3) {
                    Text(RowWords.who(row, mine: RowWords.mine(mailbox.accounts)).name)
                        .font(.meadow(.ui, 14, line.hit.seen ? .bold : .heavy))
                        .foregroundStyle(meadow.ink)
                    Text(line.hit.subject.isEmpty ? String(localized: "(no subject)") : line.hit.subject)
                        .font(.meadow(.ui, 12.5))
                        .foregroundStyle(meadow.ink3)
                }
                .lineLimit(1)
            } account: {
                MeadowChip(mailbox.name(of: line.hit.account),
                           tone: .account(meadow.account(of: line.hit.account, among: mailbox.accounts)))
            } amount: {
                LedgerAmount(line: line, size: 14)
            }
            .padding(.vertical, 12)
            .background(RoundedRectangle(cornerRadius: 8)
                .fill(isOpen ? meadow.primaryWash : hovering ? meadow.paper2 : .clear))
            .overlay(alignment: .bottom) { Rectangle().fill(meadow.line).frame(height: 1) }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .focused($focused)
        .focusEffectDisabled()
        .meadowFocusRing(focused, radius: 8)
        .onHover { hovering = $0 }
        .accessibilityElement(children: .combine)
        .accessibilityHint("Opens the receipt")
    }
}

/// A receipt beside an open one, in the list's room: who and what for over the account, the amount over the day.
private struct LedgerLine: View {
    let mailbox: Mailbox
    let line: Ledger.Line
    @Environment(\.meadow) private var meadow
    @FocusState private var focused: Bool
    @State private var hovering = false

    var body: some View {
        let (open, isOpen) = opening(line.hit, in: mailbox)
        let row = API.FolderRow(line.hit)
        Button(action: open) {
            HStack(alignment: .top, spacing: 10) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(RowWords.who(row, mine: RowWords.mine(mailbox.accounts)).name)
                        .font(.meadow(.ui, 14, line.hit.seen ? .bold : .heavy))
                        .foregroundStyle(meadow.ink)
                    Text(line.hit.subject.isEmpty ? String(localized: "(no subject)") : line.hit.subject)
                        .font(.meadow(.ui, 12.5))
                        .foregroundStyle(meadow.ink3)
                    if mailbox.accounts.count > 1 {
                        MeadowChip(mailbox.name(of: line.hit.account),
                                   tone: .account(meadow.account(of: line.hit.account, among: mailbox.accounts)))
                            .padding(.top, 3)
                    }
                }
                .lineLimit(1)
                .frame(maxWidth: .infinity, alignment: .leading)
                VStack(alignment: .trailing, spacing: 2) {
                    LedgerAmount(line: line, size: 14)
                    Text(LedgerDay.short(line.hit.date) ?? line.hit.when)
                        .font(.meadow(.ui, 12))
                        .foregroundStyle(meadow.ink3)
                }
            }
            .padding(10)
            .background(RoundedRectangle(cornerRadius: 12)
                .fill(isOpen ? meadow.primaryWash : hovering ? meadow.paper2 : .clear))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .focused($focused)
        .focusEffectDisabled()
        .meadowFocusRing(focused, radius: 12)
        .onHover { hovering = $0 }
        .accessibilityElement(children: .combine)
        .accessibilityHint("Opens the receipt")
    }
}

/// A line's amount (`td.amt`): heavy and tabular, a refund in moss with a real minus, a dash where none was read.
private struct LedgerAmount: View {
    let line: Ledger.Line
    let size: CGFloat
    @Environment(\.meadow) private var meadow

    var body: some View {
        Text(line.amount.map { Ledger.money($0.currency, line.shown ?? $0.value) } ?? "—")
            .font(.meadow(.ui, size, .heavy))
            .monospacedDigit()
            .foregroundStyle(line.amount == nil ? meadow.ink3 : line.refund ? meadow.moss : meadow.ink)
            .lineLimit(1)
    }
}

/// A receipt's day in the ledger: "Fri 30".
enum LedgerDay {
    static func short(_ date: String, calendar: Calendar = .current, locale: Locale = .current) -> String? {
        guard !date.isEmpty, let day = EventWhen.moment(date, calendar: calendar) else { return nil }
        return day.formatted(Date.FormatStyle(locale: locale, calendar: calendar, timeZone: calendar.timeZone)
            .weekday(.abbreviated).day())
    }
}
#endif
