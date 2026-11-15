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

/// When to bring the mail back, as the mockup's snooze popover asks after `h` (`snoozeMenu()`): one of the presets
/// (keys 1 to 4), or a day and an hour of a person's own; and in Snoozed, bringing it back now. Its keys come through
/// `menu`, as a popover's do.
struct SnoozeChooser: View {
    let copies: [MailCopy]
    let model: AttentionModel
    var canUnsnooze = false
    let menu: MeadowMenuModel
    let close: @MainActor () -> Void
    @State private var picking = false
    @State private var until = SnoozeChooser.tomorrowMorning()
    @Environment(\.meadow) private var meadow

    var body: some View {
        Group {
            if picking {
                ownTime
            } else {
                MeadowMenu(parts: parts, width: 260, model: menu, close: close, label: title)
            }
        }
        .onAppear { listen() }
        .onChange(of: picking) { listen() }
    }

    private var title: String {
        copies.count == 1 ? String(localized: "Snooze until") : String(localized: "Snooze \(copies.count) until")
    }

    private var parts: [MeadowMenu.Part] {
        var parts: [MeadowMenu.Part] = [.title(title)]
        for (index, preset) in API.SnoozePreset.allCases.enumerated() {
            parts.append(.item(MeadowMenuItem(id: preset.rawValue, title: Snoozing.words(preset),
                                              keys: ["\(index + 1)"]) { pick(.preset(preset)) }))
        }
        parts.append(.rule)
        parts.append(.item(MeadowMenuItem(id: "own", title: String(localized: "Pick a date & time…"), icon: .cal,
                                          closes: false) { picking = true }))
        if canUnsnooze {
            parts.append(.rule)
            parts.append(.item(MeadowMenuItem(id: "unsnooze", title: String(localized: "Unsnooze"), icon: .restore) {
                let (model, copies) = (model, copies)
                Task { await model.unsnooze(copies) }
            }))
        }
        return parts
    }

    /// The keys the chooser takes before the menu's: a preset's digit picks it, as the page's chooser numbers them;
    /// while a time of a person's own is being picked, Return snoozes until it and the arrows are the pickers'.
    private func listen() {
        if picking {
            menu.items = []
            menu.onKey = { event in
                guard event.keyCode == 36 || event.keyCode == 76 else { return false }
                snoozeUntil()
                return true
            }
        } else {
            menu.onKey = { event in
                guard let preset = Self.preset(forKey: event.charactersIgnoringModifiers ?? "") else { return false }
                close()
                pick(.preset(preset))
                return true
            }
        }
    }

    /// The preset a digit picks: 1 to 4, in the menu's order.
    static func preset(forKey key: String) -> API.SnoozePreset? {
        guard let digit = Int(key), (1...API.SnoozePreset.allCases.count).contains(digit) else { return nil }
        return API.SnoozePreset.allCases[digit - 1]
    }

    /// A day and an hour of a person's own, in the popover.
    private var ownTime: some View {
        VStack(alignment: .leading, spacing: 0) {
            MeadowPaletteSection(title, pop: true)
            // The day on a calendar, and the hour in a field beside it: the graphical clock would double the width.
            HStack(alignment: .top, spacing: 10) {
                DatePicker("Day", selection: $until, in: Date()..., displayedComponents: [.date])
                    .datePickerStyle(.graphical)
                DatePicker("Time", selection: $until, displayedComponents: [.hourAndMinute])
                    .datePickerStyle(.stepperField)
            }
            .labelsHidden()
            .padding(.horizontal, 10)
            Text(until.formatted(date: .complete, time: .shortened))
                .font(.meadow(.ui, 12.5))
                .foregroundStyle(meadow.ink3)
                .padding(.horizontal, 10)
                .padding(.top, 6)
            MeadowPopRule()
            HStack(spacing: 8) {
                Spacer()
                Button("Back") { picking = false }
                    .meadowButton(.quiet, small: true)
                Button("Snooze", action: snoozeUntil)
                    .meadowButton(.primary, small: true)
            }
            .padding(.horizontal, 4)
            .padding(.bottom, 4)
        }
        .fixedSize()
        .meadowPop()
    }

    private func snoozeUntil() {
        close()
        pick(.until(until))
    }

    private func pick(_ when: Snoozing) {
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
