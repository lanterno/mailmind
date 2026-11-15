#if os(macOS)
import Foundation

/// One of the desk's commands, for the mailbox's single keys, its ⌘K palette or a row's context menu: what it is
/// called there, the page's single key for it if it has one, and what it does.
struct DeskCommand: Identifiable {
    let id: String
    let label: String
    let key: Character?
    let symbol: String
    let run: @MainActor () -> Void
}

/// Attention's verbs on the messages chosen, as the page's commands name them and with its keys: Snooze… (h, then a
/// choice from 1 to 4), Mute or in Muted Unmute (w), Reply later or in Reply Later Done (l), and in Waiting, Dismiss
/// follow-up. What each shows follows the stack the list is.
@MainActor
enum AttentionCommands {
    static func list(copies: [MailCopy], stack: AttentionStack?, model: AttentionModel,
                     askWhen: @escaping ([MailCopy]) -> Void) -> [DeskCommand] {
        guard !copies.isEmpty else { return [] }
        let muted = stack == .muted
        let later = stack == .replyLater
        var all = [
            DeskCommand(id: "snooze", label: String(localized: "Snooze…"), key: "h", symbol: "clock") { askWhen(copies) },
            DeskCommand(id: "mute", label: muted ? String(localized: "Unmute thread") : String(localized: "Mute thread"),
                        key: "w", symbol: muted ? "bell" : "bell.slash") {
                Task { if muted { await model.unmute(copies) } else { await model.mute(copies) } }
            },
            DeskCommand(id: "later", label: later ? String(localized: "Reply later — Done") : String(localized: "Reply later"),
                        key: "l", symbol: later ? "checkmark" : "bookmark") {
                Task { if later { await model.replyLaterDone(copies) } else { await model.replyLater(copies) } }
            },
        ]
        if stack == .waiting {
            all.append(DeskCommand(id: "dismiss", label: String(localized: "Dismiss follow-up"), key: nil,
                                   symbol: "checkmark.circle") { Task { await model.dismissFollowup(copies) } })
        }
        return all
    }
}
#endif
