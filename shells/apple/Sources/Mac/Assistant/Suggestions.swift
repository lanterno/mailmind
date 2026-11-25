#if os(macOS)
import AppKit
import Observation
import SwiftUI

/// What the open message proposes, found by the AI on a click (`/api/ai/extract`), as the page's Suggestions shows
/// it. Each is a suggestion: its fields can be changed, and nothing is made until someone confirms it. An event becomes
/// a calendar file that opens in the person's calendar app and invites no one; a task joins their list.
@MainActor
@Observable
final class SuggestionCards {
    enum Card: Equatable {
        case reading
        case failed(String)
        case found(API.Extracted)
    }

    struct Shown: Equatable {
        let copy: MailCopy
        let card: Card
    }

    private(set) var shown: Shown?
    @ObservationIgnored private var asking: Set<MailCopy> = []
    @ObservationIgnored let link: CoreLink

    init(link: CoreLink) {
        self.link = link
    }

    func card(for copy: MailCopy) -> Card? {
        shown?.copy == copy ? shown?.card : nil
    }

    /// Ask once however many times it's asked, and show what was found in the reader.
    func find(in copy: MailCopy) async {
        guard let routes = link.routes, !asking.contains(copy) else { return }
        asking.insert(copy)
        defer { asking.remove(copy) }
        shown = Shown(copy: copy, card: .reading)
        do {
            let found = try await routes.post(API.Post.AiExtract.self, copy.namesAMessage, asking: link.asksOverLimit)
            shown = Shown(copy: copy, card: .found(found))
        } catch {
            let why = DeskRefusal.isAIOff(error)
                ? String(localized: "AI is off, so nothing can read this for dates.")
                : DeskRefusal.isOverLimit(error)
                    ? String(localized: "Today’s AI limit is reached, so nothing can read this for dates.")
                    : DeskRefusal.words(error, else: String(localized: "Couldn’t read this message for dates."))
            shown = Shown(copy: copy, card: .failed(why))
        }
    }

    /// The calendar file for an event someone confirmed, each field as they left it. A refusal is thrown with the
    /// server's words for what is missing or won't do.
    func event(from copy: MailCopy, title: String, start: String, end: String, place: String) async throws -> API.EventFile {
        guard let routes = link.routes else { throw CancellationError() }
        return try await routes.post(API.Post.CalendarEvent.self, API.EventBody(
            end: end, folder: copy.folder, key: copy.key, place: place, start: start, title: title, uid: copy.uid))
    }

    /// Add a task someone confirmed to their list.
    func addTask(from copy: MailCopy, title: String, due: String) async throws {
        guard let routes = link.routes else { throw CancellationError() }
        _ = try await routes.post(API.Post.Tasks.self, API.TaskBody(
            due: due, folder: copy.folder, key: copy.key, title: title, uid: copy.uid))
    }
}

/// A suggestion's time as a form shows it and as the server reads it back, as the page's `toField` and `fromField`
/// do. A day stays a day; a time the text named in a zone shows here as it falls here; a time named in no zone is its
/// clock reading here; and a time goes back as the instant it is.
enum SuggestedTime {
    /// The date a picker starts on for `moment`, or nil when it names none.
    static func field(_ moment: String?, allDay: Bool, in zone: TimeZone = .current) -> Date? {
        guard let moment, !moment.isEmpty else { return nil }
        if allDay {
            return day(String(moment.prefix(10)), in: zone)
        }
        if moment.range(of: #"(?:Z|[+-]\d{2}:?\d{2})$"#, options: [.regularExpression, .caseInsensitive]) != nil {
            return instant(moment)
        }
        // A wall-clock time: its reading here.
        return formatter("yyyy-MM-dd'T'HH:mm", zone).date(from: String(moment.prefix(16)))
    }

    /// What a form's date says as the server reads it: a day as `yyyy-MM-dd` in this zone, a time as its instant.
    static func sent(_ date: Date, allDay: Bool, in zone: TimeZone = .current) -> String {
        if allDay {
            return formatter("yyyy-MM-dd", zone).string(from: date)
        }
        let instant = ISO8601DateFormatter()
        instant.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return instant.string(from: date)
    }

    /// A day, `yyyy-MM-dd`, at its first moment in `zone`.
    static func day(_ text: String, in zone: TimeZone = .current) -> Date? {
        formatter("yyyy-MM-dd", zone).date(from: text)
    }

    private static func instant(_ text: String) -> Date? {
        let instant = ISO8601DateFormatter()
        if let date = instant.date(from: text) { return date }
        instant.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return instant.date(from: text)
    }

    private static func formatter(_ format: String, _ zone: TimeZone) -> DateFormatter {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.timeZone = zone
        formatter.dateFormat = format
        return formatter
    }
}

/// The card of what a message proposes, with a form for each event and task found.
struct SuggestionCard: View {
    let copy: MailCopy
    let cards: SuggestionCards
    let notices: DeskNotices
    @Environment(\.meadow) private var meadow

    var body: some View {
        switch cards.card(for: copy) {
        case nil:
            EmptyView()
        case .reading:
            DeskCard {
                DeskCardHeading(title: String(localized: "Found in this message"), icon: .todo, ai: true)
                DeskWaitingBars(lines: 2, saying: "Reading for dates and tasks…")
            }
        case .failed(let why):
            DeskCard {
                HStack(alignment: .firstTextBaseline) {
                    Text(why).foregroundStyle(meadow.rust)
                    Button("Retry") { Task { await cards.find(in: copy) } }.deskLink()
                }
                .font(.meadow(.ui, 13.5))
            }
        case .found(let found):
            DeskCard {
                DeskCardHeading(title: String(localized: "Found in this message"), icon: .todo, ai: true)
                FoundLists(copy: copy, found: found, cards: cards, notices: notices)
                    .id(found)
                DeskCardFoot { Text(verbatim: String(localized: "Nothing is added until you confirm it") + " · \(found.model)") }
            }
        }
    }
}

/// The events and tasks still to confirm or dismiss. Each leaves the list when confirmed or dismissed; the model's
/// order names each, as the same words can come twice.
private struct FoundLists: View {
    let copy: MailCopy
    let found: API.Extracted
    let cards: SuggestionCards
    let notices: DeskNotices
    @State private var gone: Set<String> = []

    var body: some View {
        let events = found.events.enumerated().map { ("e\($0.offset)", $0.element) }.filter { !gone.contains($0.0) }
        let tasks = found.tasks.enumerated().map { ("t\($0.offset)", $0.element) }.filter { !gone.contains($0.0) }
        if events.isEmpty && tasks.isEmpty {
            Text(found.events.count + found.tasks.count > 0
                 ? "That’s everything it found." : "Nothing here proposes a meeting or a dated task.")
                .font(.meadow(.ui, 13.5))
        }
        ForEach(events, id: \.0) { id, event in
            EventForm(copy: copy, event: event, cards: cards, notices: notices) { gone.insert(id) }
        }
        ForEach(tasks, id: \.0) { id, task in
            TaskForm(copy: copy, task: task, cards: cards, notices: notices) { gone.insert(id) }
        }
    }
}

/// An event someone can change, then add to their calendar.
struct EventForm: View {
    let copy: MailCopy
    let event: API.SuggestedEvent
    let cards: SuggestionCards
    let notices: DeskNotices
    let drop: () -> Void
    @Environment(\.meadow) private var meadow
    @State private var title: String
    @State private var start: Date
    @State private var end: Date?
    @State private var place: String
    @State private var refused = ""

    init(copy: MailCopy, event: API.SuggestedEvent, cards: SuggestionCards, notices: DeskNotices, drop: @escaping () -> Void) {
        self.copy = copy
        self.event = event
        self.cards = cards
        self.notices = notices
        self.drop = drop
        _title = State(initialValue: event.title)
        let start = SuggestedTime.field(event.start, allDay: event.allDay) ?? AppSeams.clock.now
        _start = State(initialValue: start)
        _end = State(initialValue: SuggestedTime.field(event.end, allDay: event.allDay))
        _place = State(initialValue: event.place)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            FormLabel(title: String(localized: "Event"), icon: .cal)
            Form {
                TextField("Title", text: $title)
                DatePicker("Starts", selection: $start, displayedComponents: components)
                DatePicker("Ends", selection: Binding(get: { end ?? start }, set: { end = $0 }),
                           displayedComponents: components)
                TextField("Place", text: $place)
            }
            .formStyle(.columns)
            if !refused.isEmpty {
                Text(refused).font(.meadow(.ui, 13)).foregroundStyle(meadow.rust)
            }
            HStack {
                Button("Add to Calendar") { Task { await confirm() } }
                    .meadowButton(.primary, small: true)
                Button("Dismiss", action: drop).meadowButton(.quiet, small: true)
            }
            .controlSize(.small)
        }
        .padding(10)
        .background(RoundedRectangle(cornerRadius: 10).fill(meadow.paper2))
    }

    private var components: DatePickerComponents {
        event.allDay ? [.date] : [.date, .hourAndMinute]
    }

    private func confirm() async {
        refused = ""
        do {
            let file = try await cards.event(
                from: copy, title: String(title.prefix(200)), start: SuggestedTime.sent(start, allDay: event.allDay),
                end: end.map { SuggestedTime.sent($0, allDay: event.allDay) } ?? "", place: String(place.prefix(300)))
            try CalendarFile.open(file.ics, named: file.filename)
            notices.say(String(localized: "Opened in your calendar app — add it there"))
            drop()
        } catch {
            refused = DeskRefusal.words(error, else: String(localized: "Couldn’t make the file."))
        }
    }
}

/// A task someone can change, then add to their list.
struct TaskForm: View {
    let copy: MailCopy
    let task: API.SuggestedTask
    let cards: SuggestionCards
    let notices: DeskNotices
    let drop: () -> Void
    @Environment(\.meadow) private var meadow
    @State private var title: String
    @State private var due: Date?
    @State private var refused = ""

    init(copy: MailCopy, task: API.SuggestedTask, cards: SuggestionCards, notices: DeskNotices, drop: @escaping () -> Void) {
        self.copy = copy
        self.task = task
        self.cards = cards
        self.notices = notices
        self.drop = drop
        _title = State(initialValue: task.title)
        _due = State(initialValue: task.due.flatMap { SuggestedTime.day(String($0.prefix(10))) })
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            FormLabel(title: String(localized: "Task"), icon: .checkCircle)
            Form {
                TextField("Title", text: $title)
                LabeledContent("Due") {
                    HStack {
                        Toggle("Has a due date", isOn: Binding(get: { due != nil }, set: { due = $0 ? (due ?? AppSeams.clock.now) : nil }))
                            .labelsHidden()
                        if let day = due {
                            DatePicker("Due", selection: Binding(get: { day }, set: { due = $0 }), displayedComponents: .date)
                                .labelsHidden()
                        }
                    }
                }
            }
            .formStyle(.columns)
            if !refused.isEmpty {
                Text(refused).font(.meadow(.ui, 13)).foregroundStyle(meadow.rust)
            }
            HStack {
                Button("Add to Tasks") { Task { await confirm() } }
                    .meadowButton(.primary, small: true)
                Button("Dismiss", action: drop).meadowButton(.quiet, small: true)
            }
            .controlSize(.small)
        }
        .padding(10)
        .background(RoundedRectangle(cornerRadius: 10).fill(meadow.paper2))
    }

    private func confirm() async {
        refused = ""
        do {
            try await cards.addTask(from: copy, title: String(title.prefix(200)),
                                    due: due.map { SuggestedTime.sent($0, allDay: true) } ?? "")
            notices.say(String(localized: "Added to your tasks"))
            drop()
        } catch {
            refused = DeskRefusal.words(error, else: String(localized: "Couldn’t add that task."))
        }
    }
}

/// What a form makes, over it: a small icon and a label in small capitals.
private struct FormLabel: View {
    let title: String
    let icon: MeadowIcon
    @Environment(\.meadow) private var meadow

    var body: some View {
        HStack(spacing: 6) {
            MeadowIconView(icon, size: 14).foregroundStyle(meadow.ink3)
            MeadowSectionLabel(title)
        }
    }
}

/// A calendar file a person asked for, opened in their calendar app, which asks them where to add it. It is written
/// to a folder of its own in the temporary directory, never beside the data.
enum CalendarFile {
    @MainActor static func open(_ text: String, named name: String) throws {
        try open(Data(text.utf8), named: name)
    }

    @MainActor static func open(_ data: Data, named name: String) throws {
        let folder = FileManager.default.temporaryDirectory.appending(path: "MailMind Calendar Files", directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let file = folder.appending(path: safeName(name))
        try data.write(to: file, options: .atomic)
        AppSeams.links.open(file)
    }

    /// A file name with nothing in it that leaves the folder, ending in .ics.
    static func safeName(_ name: String) -> String {
        let kept = name.replacingOccurrences(of: "/", with: "-").replacingOccurrences(of: ":", with: "-")
            .trimmingCharacters(in: CharacterSet(charactersIn: ". ").union(.whitespacesAndNewlines))
        let base = kept.isEmpty ? "event" : kept
        return base.lowercased().hasSuffix(".ics") ? base : base + ".ics"
    }
}
#endif
