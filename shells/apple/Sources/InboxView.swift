import SwiftUI

struct InboxView: View {
    let demo: Demo

    var body: some View {
        NavigationStack {
            content
                .navigationTitle("Inbox")
        }
    }

    @ViewBuilder private var content: some View {
        switch demo.stage {
        case .starting:
            Color.clear
        case .makingMailbox:
            ProgressView("Making a fictional mailbox of 100,000 messages")
        case .failed(let why):
            ContentUnavailableView("The mailbox did not open", systemImage: "exclamationmark.triangle",
                                   description: Text(why))
        case .open:
            MessageList(model: demo.list)
        }
    }
}

/// The list holds positions only: each row is its position, and shows what the core sent for it or a placeholder.
/// The table is AppKit's or UIKit's (`MessageTable`): SwiftUI's `List` could not keep a deep scroll within a frame.
struct MessageList: View {
    let model: ListModel

    var body: some View {
        MessageTable(model: model)
            .task(id: model.firstRowsAt) {
                if model.firstRowsAt != nil { await Measure.shared.firstRowsShown(model) }
            }
    }
}

/// A row's date as a mail list shows it: the time today, the weekday this week, the day this year, else the full date.
enum RowDate {
    static func text(_ seconds: Int64, now: Date = .now, calendar: Calendar = .current) -> String {
        guard seconds != 0 else { return "" }
        let date = Date(timeIntervalSince1970: TimeInterval(seconds))
        if calendar.isDate(date, inSameDayAs: now) {
            return date.formatted(date: .omitted, time: .shortened)
        }
        if calendar.isDateInYesterday(date) {
            return String(localized: "Yesterday")
        }
        if let days = calendar.dateComponents([.day], from: date, to: now).day, days >= 0, days < 7 {
            return date.formatted(.dateTime.weekday(.abbreviated))
        }
        if calendar.isDate(date, equalTo: now, toGranularity: .year) {
            return date.formatted(.dateTime.month(.abbreviated).day())
        }
        return date.formatted(date: .numeric, time: .omitted)
    }
}
