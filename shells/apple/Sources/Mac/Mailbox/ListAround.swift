#if os(macOS)
import SwiftUI

/// The window's list of mail, today's brief first in the Inbox.
struct MailboxTable: View {
    let list: MailListModel
    let mailbox: Mailbox
    /// The rows chosen as the mailbox holds them.
    let chosen: [MailCopy]
    /// Whether each row says its account: everywhere but one account's own folder.
    let showsAccount: Bool
    var status = AppStatus.shared
    @Environment(\.meadow) private var meadow
    @Environment(\.meadowLight) private var light
    @Environment(Desk.self) private var desk: Desk?

    var body: some View {
        let place = mailbox.app?.place
        let measure = RowMeasure(status.preferences?.density ?? .comfortable)
        MailboxRowsTable(list: list, mailbox: mailbox, chosen: chosen, showsAccount: showsAccount,
                         look: RowLook(inks: RowInks.of(meadow, light: light,
                                                        highContrast: status.preferences?.highContrast ?? false),
                                       measure: measure, accounts: mailbox.accounts),
                         brief: briefLine(place), briefChosen: place == .desk(.brief),
                         sections: DayGroup.sections(place))
    }

    /// Today's brief over the Inbox's rows (`BriefRow`): in every Inbox, or one account's, while not searching.
    private func briefLine(_ place: Place?) -> BriefLine? {
        guard let desk, let place, place.listed.isInbox else { return nil }
        return BriefLine(needs: desk.brief.needs, waiting: mailbox.across?.waiting ?? 0,
                         canWait: desk.brief.tidy.count)
    }
}
#endif
