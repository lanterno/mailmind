#if os(macOS)
import Foundation
import Observation

/// A lane's search (discovery/Lanes.tsx): Feed's newsletters and Paper Trail's receipts, read 200 at a time and 200
/// more when asked, as the page's list reads a search. A lane shows its hits as cards or a ledger, not as rows, so it
/// keeps them as the server sent them: a ledger reads their dates.
@MainActor
@Observable
final class LaneModel {
    let search: Search
    private(set) var hits: [API.SearchHit] = []
    private(set) var hasMore = false
    /// Set once the first read has come.
    private(set) var loaded = false
    /// It held hits and holds none now: what was there was filed or deleted, so it isn't empty "yet".
    private(set) var emptied = false
    private(set) var problem: String?
    /// Run once, as the first read is done or refused: what waits on knowing which hits the lane holds.
    @ObservationIgnored var onceLoaded: (@MainActor () -> Void)?

    @ObservationIgnored private let wire: Wire
    @ObservationIgnored private let scope: API.SearchScope
    @ObservationIgnored private var reach = Lists.pageSize
    @ObservationIgnored private var reading = 0

    init(search: Search, scope: API.SearchScope, wire: Wire) {
        self.search = search
        self.scope = scope
        self.wire = wire
    }

    /// Read the lane again from its top, as far as it has been read.
    func refresh() {
        reading += 1
        let number = reading, wire = wire, search = search, scope = scope, reach = reach
        Task {
            var hits: [API.SearchHit] = []
            var seen = Set<String>()
            var more = false
            var at = 0
            do {
                repeat {
                    let page = try await Lists.hits(wire, search, scope: scope, offset: at, limit: Lists.pageSize)
                    for hit in page.hits where seen.insert("\(hit.key):\(hit.uid):\(hit.folder)").inserted {
                        hits.append(hit)
                    }
                    more = page.hasMore && !page.hits.isEmpty
                    at += Lists.pageSize
                } while more && at < reach
            } catch {
                guard number == reading else { return }
                problem = (error as? Problem)?.description ?? String(localized: "Search failed.")
                read()
                return
            }
            guard number == reading else { return }
            emptied = hits.isEmpty && (emptied || !self.hits.isEmpty)
            self.hits = hits
            hasMore = more
            problem = nil
            read()
        }
    }

    private func read() {
        loaded = true
        if let once = onceLoaded {
            onceLoaded = nil
            once()
        }
    }

    /// Read 200 more.
    func more() {
        reach += Lists.pageSize
        refresh()
    }

    /// Change the hit for `copy` now, as the server has just changed it, until the lane is read again.
    func patch(_ copy: MailCopy, _ change: (inout API.SearchHit) -> Void) {
        guard let at = hits.firstIndex(where: { MailCopy(API.FolderRow($0)) == copy }) else { return }
        change(&hits[at])
    }
}

/// The Document Hub (discovery/Docs.tsx): every attachment across the accounts, which the server filters, groups and
/// pages, so a search reaches every file and not only those read.
@MainActor
@Observable
final class DocumentsModel {
    /// How the files are shown: in sections by sender or by type, or newest first.
    enum Showing: String, CaseIterable, Identifiable {
        case sender
        case type
        case newest

        var id: String { rawValue }

        var title: String {
            switch self {
            case .sender: String(localized: "By sender")
            case .type: String(localized: "By type")
            case .newest: String(localized: "Newest")
            }
        }

        var grouping: API.DocumentGrouping? {
            switch self {
            case .sender: .sender
            case .type: .type
            case .newest: nil
            }
        }
    }

    /// The kind of file shown, or every kind.
    var kind: API.DocumentKind? {
        didSet { if kind != oldValue { reload() } }
    }
    var showing = Showing.sender {
        didSet { if showing != oldValue { reload() } }
    }
    /// What is typed in the hub's own search, read a moment after the typing stops.
    var typed = "" {
        didSet { if typed != oldValue { soon() } }
    }

    private(set) var docs: [API.Document] = []
    /// The first page's answer: the facets, the sections, how many match and how many there are.
    private(set) var first: API.Documents?
    private(set) var failed = false
    private(set) var readingMore = false

    @ObservationIgnored private let wire: Wire
    @ObservationIgnored private var read = 0
    @ObservationIgnored private var reading = 0
    @ObservationIgnored private var typing: Task<Void, Never>?

    init(wire: Wire) {
        self.wire = wire
    }

    /// How many more match than have been read.
    var left: Int {
        max(0, (first?.matched ?? 0) - read)
    }

    /// A section's name and count, by the id a file names it with.
    func section(_ id: String) -> API.DocumentSection? {
        first?.groups.first { $0.id == id }
    }

    /// The heading a file goes under: its section's name, or its kind's.
    func heading(of doc: API.Document) -> String {
        showing == .type ? Self.label(doc.kind) : section(doc.group).map(\.name).flatMap { $0.isEmpty ? nil : $0 }
            ?? String(localized: "No sender")
    }

    /// What the hub counts (`docCount`): the files shown, of how many match.
    var countText: String {
        guard let first else { return "" }
        let n = first.matched
        return "\(docs.count)\(left > 0 ? " of \(n)" : "") file\(n == 1 ? "" : "s")"
    }

    /// Read from the first page again.
    func reload() {
        reading += 1
        read = 0
        let number = reading
        Task {
            do {
                let page = try await page(offset: 0)
                guard number == reading else { return }
                first = page
                docs = []
                take(page)
                failed = false
            } catch {
                guard number == reading else { return }
                failed = true
            }
        }
    }

    /// The next page: from how far through the server's list, not how many are held, as new mail moves the rest along.
    func more() {
        guard !readingMore else { return }
        readingMore = true
        let number = reading
        Task {
            defer { readingMore = false }
            guard let page = try? await page(offset: read), number == reading else { return }
            take(page)
        }
    }

    private func take(_ page: API.Documents) {
        read += page.docs.count
        var seen = Set(docs.map(Self.id))
        docs += page.docs.filter { seen.insert(Self.id($0)).inserted }
    }

    private func soon() {
        typing?.cancel()
        typing = Task {
            try? await AppSeams.clock.sleep(for: .milliseconds(120))
            guard !Task.isCancelled else { return }
            reload()
        }
    }

    private func page(offset: Int) async throws -> API.Documents {
        let q = typed.trimmingCharacters(in: .whitespaces)
        return try await wire.get(API.Get.Documents.self, [
            "limit": String(Lists.pageSize), "offset": String(offset), "kind": kind?.rawValue,
            "q": q.isEmpty ? nil : q, "group": showing.grouping?.rawValue,
        ])
    }

    /// One file, as the hub names it apart from the others.
    static func id(_ doc: API.Document) -> String {
        "\(doc.key):\(doc.uid):\(doc.folder)/\(doc.partId)/\(doc.filename)"
    }

    /// A kind's words, in the order the facets show them (`LABEL`).
    static func label(_ kind: API.DocumentKind) -> String {
        switch kind {
        case .pdf: String(localized: "PDFs")
        case .image: String(localized: "Images")
        case .doc: String(localized: "Docs")
        case .sheet: String(localized: "Sheets")
        case .slide: String(localized: "Slides")
        case .archive: String(localized: "Archives")
        case .text: String(localized: "Text")
        case .other: String(localized: "Other")
        }
    }

    /// Meadow's icon for a kind of file.
    static func icon(_ kind: API.DocumentKind) -> MeadowIcon {
        switch kind {
        case .pdf, .doc, .text: .file
        case .image: .image
        case .sheet: .table
        case .slide: .slides
        case .archive: .archive
        case .other: .clip
        }
    }
}
#endif
