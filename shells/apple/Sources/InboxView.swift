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
struct MessageList: View {
    let model: ListModel

    var body: some View {
        ScrollViewReader { scroller in
            List(0..<model.total, id: \.self) { index in
                MessageRowView(row: model.held[index])
                    .onAppear { model.appeared(index) }
                    .onDisappear { model.disappeared(index) }
            }
            .listStyle(.plain)
            .task(id: model.firstRowsAt) {
                if model.firstRowsAt != nil { await Measure.shared.firstRowsShown(model, scroller) }
            }
        }
    }
}

struct MessageRowView: View {
    let row: MessageRow?

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Circle()
                .fill(row?.unread == true ? Color.accentColor : .clear)
                .frame(width: 8, height: 8)
            if let row {
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 6) {
                        Text(row.sender)
                            .fontWeight(row.unread ? .semibold : .regular)
                            .lineLimit(1)
                        Spacer(minLength: 4)
                        if row.attachment { Image(systemName: "paperclip").foregroundStyle(.secondary) }
                        if row.flagged { Image(systemName: "flag.fill").foregroundStyle(.orange) }
                        Text(RowDate.text(row.date))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Text(row.subject).font(.subheadline).lineLimit(1)
                    Text(row.snippet).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
            } else {
                Placeholder()
            }
        }
        .frame(height: 56)
    }
}

/// Grey bars where a row's text will go, until its batch arrives.
private struct Placeholder: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            bar(width: 140)
            bar(width: 240)
            bar(width: 320)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func bar(width: CGFloat) -> some View {
        RoundedRectangle(cornerRadius: 3).fill(.quaternary).frame(maxWidth: width).frame(height: 9)
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
