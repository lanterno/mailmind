#if os(macOS)
import SwiftUI

/// The attention verbs for the messages chosen, as menu items: in the menu bar's Message menu and a row's context
/// menu alike. Which verbs show follows the stack the list is (`stack`), as the page's reader does:
/// Unsnooze in Snoozed, Unmute in Muted, Done in Reply Later, Dismiss in Waiting.
struct AttentionMenuItems: View {
    let copies: [MailCopy]
    let stack: AttentionStack?
    let model: AttentionModel
    /// The menu bar's items carry their keyboard shortcuts; a context menu's don't repeat them.
    var shortcuts = false

    var body: some View {
        SnoozeMenu(copies: copies, stack: stack, model: model, shortcuts: shortcuts)
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

/// When to bring it back: a preset, or a time of a person's own, asked over the window (`SnoozeTimeDialog`); and in
/// Snoozed, bringing it back now.
struct SnoozeMenu: View {
    let copies: [MailCopy]
    let stack: AttentionStack?
    let model: AttentionModel
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
            Button("Custom…") { model.choosingTime = copies }
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

/// When to bring the mail back, as the mockup's snooze popover asks after `h` (`snoozeMenu()`): one of the presets
/// (keys 1 to 4), or a day and an hour of a person's own, which the window asks in a dialog of its own; and in
/// Snoozed, bringing it back now. Its keys come through `menu`, as a popover's do.
struct SnoozeChooser: View {
    let copies: [MailCopy]
    let model: AttentionModel
    var canUnsnooze = false
    let menu: MeadowMenuModel
    let close: @MainActor () -> Void

    var body: some View {
        MeadowMenu(parts: parts, width: 260, model: menu, close: close, label: Self.title(copies.count))
            .onAppear { listen() }
    }

    /// What the chooser and the dialog for a time of one's own are headed.
    static func title(_ count: Int) -> String {
        count == 1 ? String(localized: "Snooze until") : String(localized: "Snooze \(count) until")
    }

    private var parts: [MeadowMenu.Part] {
        var parts: [MeadowMenu.Part] = [.title(Self.title(copies.count))]
        for (index, preset) in API.SnoozePreset.allCases.enumerated() {
            parts.append(.item(MeadowMenuItem(id: preset.rawValue, title: Snoozing.words(preset),
                                              keys: ["\(index + 1)"]) { pick(.preset(preset)) }))
        }
        parts.append(.rule)
        // The popover closes first, and the window asks the time in its dialog, which can hold the keyboard.
        parts.append(.item(MeadowMenuItem(id: "own", title: String(localized: "Pick a date & time…"), icon: .cal) {
            [model, copies] in model.choosingTime = copies
        }))
        if canUnsnooze {
            parts.append(.rule)
            parts.append(.item(MeadowMenuItem(id: "unsnooze", title: String(localized: "Unsnooze"), icon: .restore) {
                let (model, copies) = (model, copies)
                Task { await model.unsnooze(copies) }
            }))
        }
        return parts
    }

    /// The keys the chooser takes before the menu's: a preset's digit picks it, as the page's chooser numbers them.
    private func listen() {
        menu.onKey = { event in
            guard let preset = Self.preset(forKey: SingleKey.typed(event).map(String.init) ?? "") else { return false }
            close()
            pick(.preset(preset))
            return true
        }
    }

    /// The preset a digit picks: 1 to 4, in the menu's order.
    static func preset(forKey key: String) -> API.SnoozePreset? {
        guard let digit = Int(key), (1...API.SnoozePreset.allCases.count).contains(digit) else { return nil }
        return API.SnoozePreset.allCases[digit - 1]
    }

    private func pick(_ when: Snoozing) {
        let (model, copies) = (model, copies)
        Task { await model.snooze(copies, when) }
    }

    /// Where a time of one's own starts: tomorrow at nine.
    static func tomorrowMorning(from now: Date = AppSeams.clock.now, calendar: Calendar = .current) -> Date {
        let tomorrow = calendar.date(byAdding: .day, value: 1, to: now) ?? now
        return calendar.date(bySettingHour: 9, minute: 0, second: 0, of: tomorrow) ?? tomorrow
    }
}

/// A day and an hour of a person's own to bring the mail back at, in Meadow's dialog over the window, as ⇧⌘H and the
/// chooser's "Pick a date & time…" ask it. Like Move To, it holds the keyboard while it is up: the time is typed in
/// its field and the arrows step it, or walk the calendar once that is clicked, and never reach the list behind;
/// Return snoozes and Esc closes.
struct SnoozeTimeDialog: View {
    let copies: [MailCopy]
    let model: AttentionModel
    let close: @MainActor () -> Void
    @State private var until = SnoozeChooser.tomorrowMorning()
    @FocusState private var typing: Bool
    @Environment(\.meadow) private var meadow

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 8) {
                Text(SnoozeChooser.title(copies.count))
                    .font(.meadow(.display, 18, .bold))
                    .foregroundStyle(meadow.ink)
                    .accessibilityAddTraits(.isHeader)
                Spacer()
                Button(action: close) { MeadowIconView(.x) }
                    .meadowButton(.quiet, icon: true)
                    .accessibilityLabel("Close")
            }
            .padding(.top, 14)
            .padding(.horizontal, 16)
            .padding(.bottom, 10)
            MeadowFieldRow(label: String(localized: "Time")) {
                DatePicker("Time", selection: $until, in: AppSeams.clock.now...,
                           displayedComponents: [.date, .hourAndMinute])
                    .datePickerStyle(.stepperField)
                    .labelsHidden()
                    .focused($typing)
            }
            MeadowFieldRow(label: String(localized: "Day")) {
                DatePicker("Day", selection: $until, in: AppSeams.clock.now..., displayedComponents: [.date])
                    .datePickerStyle(.graphical)
                    .labelsHidden()
                    .padding(.vertical, 6)
            }
            HStack(spacing: 8) {
                Text(until.formatted(date: .complete, time: .shortened))
                    .font(.meadow(.ui, 12.5))
                    .foregroundStyle(meadow.ink3)
                    .lineLimit(1)
                Spacer()
                Button("Cancel", action: close)
                    .meadowButton(.quiet)
                    .keyboardShortcut(.cancelAction)
                Button("Snooze", action: snooze)
                    .meadowButton(.primary)
                    .keyboardShortcut(.defaultAction)
            }
            .padding(.vertical, 12)
            .padding(.horizontal, 16)
            .overlay(alignment: .top) { Rectangle().fill(meadow.line).frame(height: 1) }
        }
        .onAppear {
            typing = true
            // Asked again once the dialog is up: the list lets the keyboard go only as the dialog comes.
            Task { @MainActor in typing = true }
        }
        .accessibilityLabel(SnoozeChooser.title(copies.count))
    }

    private func snooze() {
        let (model, copies, until) = (model, copies, until)
        close()
        Task { await model.snooze(copies, .until(until)) }
    }
}
#endif
