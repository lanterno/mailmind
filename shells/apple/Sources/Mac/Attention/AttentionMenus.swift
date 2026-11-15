#if os(macOS)
import SwiftUI

/// The attention verbs for the messages chosen, as menu items: in the menu bar's Message menu and a row's context
/// menu alike. Which verbs show follows the stack the list is (`stack`), as the page's reader does:
/// Unsnooze in Snoozed, Unmute in Muted, Done in Reply Later, Dismiss in Waiting.
struct AttentionMenuItems: View {
    let copies: [MailCopy]
    let stack: AttentionStack?
    let model: AttentionModel
    /// Asks for a time of a person's own (the sheet with a date picker).
    let askUntil: ([MailCopy]) -> Void
    /// The menu bar's items carry their keyboard shortcuts; a context menu's don't repeat them.
    var shortcuts = false

    var body: some View {
        SnoozeMenu(copies: copies, stack: stack, model: model, askUntil: askUntil, shortcuts: shortcuts)
        Button(stack == .muted ? "Unmute Thread" : "Mute Thread", systemImage: stack == .muted ? "bell" : "bell.slash") {
            run { model in
                if stack == .muted { await model.unmute(copies) } else { await model.mute(copies) }
            }
        }
        .keyboardShortcut(shortcuts ? KeyboardShortcut("m", modifiers: [.control, .command]) : nil)
        Button(stack == .replyLater ? "Reply Later — Done" : "Reply Later",
               systemImage: stack == .replyLater ? "checkmark" : "bookmark") {
            run { model in
                if stack == .replyLater { await model.replyLaterDone(copies) } else { await model.replyLater(copies) }
            }
        }
        .keyboardShortcut(shortcuts ? KeyboardShortcut("l", modifiers: [.control, .command]) : nil)
        if stack == .waiting {
            Button("Dismiss Follow-Up", systemImage: "checkmark.circle") { run { await $0.dismissFollowup(copies) } }
        }
    }

    private func run(_ verb: @escaping @MainActor (AttentionModel) async -> Void) {
        let model = model
        Task { await verb(model) }
    }
}

/// When to bring it back: a preset, or a time of a person's own; and in Snoozed, bringing it back now.
struct SnoozeMenu: View {
    let copies: [MailCopy]
    let stack: AttentionStack?
    let model: AttentionModel
    let askUntil: ([MailCopy]) -> Void
    var shortcuts = false

    var body: some View {
        Menu("Snooze", systemImage: "clock") {
            ForEach(API.SnoozePreset.allCases, id: \.self) { preset in
                Button(Snoozing.words(preset)) {
                    let (model, copies) = (model, copies)
                    Task { await model.snooze(copies, .preset(preset)) }
                }
            }
            Divider()
            Button("Custom…") { askUntil(copies) }
                .keyboardShortcut(shortcuts ? KeyboardShortcut("h", modifiers: [.shift, .command]) : nil)
            if stack == .snoozed {
                Divider()
                Button("Unsnooze") {
                    let (model, copies) = (model, copies)
                    Task { await model.unsnooze(copies) }
                }
            }
        }
        .disabled(copies.isEmpty)
    }
}

/// When to bring the mail back, as the page's snooze chooser asks after `h`: one of the presets (keys 1 to 4), or a
/// day and an hour of a person's own; and in Snoozed, bringing it back now.
struct SnoozeUntilSheet: View {
    let copies: [MailCopy]
    let model: AttentionModel
    var canUnsnooze = false
    @Environment(\.dismiss) private var dismiss
    @State private var until = SnoozeUntilSheet.tomorrowMorning()

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Label(copies.count == 1 ? "Snooze Until" : "Snooze \(copies.count) Messages Until", systemImage: "clock")
                .font(.headline)
            Grid(horizontalSpacing: 8, verticalSpacing: 8) {
                ForEach(Self.rows, id: \.self) { row in
                    GridRow {
                        ForEach(row, id: \.self) { preset in
                            presetButton(preset)
                        }
                    }
                }
            }
            .controlSize(.large)
            Divider()
            DatePicker("Comes back", selection: $until, in: Date()..., displayedComponents: [.date, .hourAndMinute])
                .datePickerStyle(.graphical)
                .labelsHidden()
            Text(until.formatted(date: .complete, time: .shortened))
                .font(.callout)
                .foregroundStyle(.secondary)
            HStack {
                if canUnsnooze {
                    Button("Unsnooze") {
                        dismiss()
                        let (model, copies) = (model, copies)
                        Task { await model.unsnooze(copies) }
                    }
                }
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button("Snooze") { pick(.until(until)) }
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding(20)
        .frame(width: 400)
    }

    /// The presets two to a row, in the page's order.
    private static let rows: [[API.SnoozePreset]] = stride(from: 0, to: API.SnoozePreset.allCases.count, by: 2).map {
        Array(API.SnoozePreset.allCases[$0..<min($0 + 2, API.SnoozePreset.allCases.count)])
    }

    /// A preset's button, with the digit that picks it, as the page's chooser numbers them.
    private func presetButton(_ preset: API.SnoozePreset) -> some View {
        let digit = (API.SnoozePreset.allCases.firstIndex(of: preset) ?? 0) + 1
        return Button { pick(.preset(preset)) } label: {
            HStack {
                Text(Snoozing.words(preset))
                Spacer(minLength: 4)
                Text(verbatim: "\(digit)").foregroundStyle(.tertiary).monospacedDigit()
            }
            .frame(maxWidth: .infinity)
        }
        .keyboardShortcut(KeyEquivalent(Character("\(digit)")), modifiers: [])
    }

    private func pick(_ when: Snoozing) {
        dismiss()
        let (model, copies) = (model, copies)
        Task { await model.snooze(copies, when) }
    }

    /// Where the picker starts: tomorrow at nine.
    static func tomorrowMorning(from now: Date = Date(), calendar: Calendar = .current) -> Date {
        let tomorrow = calendar.date(byAdding: .day, value: 1, to: now) ?? now
        return calendar.date(bySettingHour: 9, minute: 0, second: 0, of: tomorrow) ?? tomorrow
    }
}
#endif
