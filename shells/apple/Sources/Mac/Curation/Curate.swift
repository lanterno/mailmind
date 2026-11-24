#if os(macOS)
import SwiftUI

/// Curate: the page's drawer for looking after the mailbox, as a sheet on paper with a page for each of its parts.
struct CurateSheet: View {
    enum Part: String, CaseIterable, Identifiable {
        case senders, inboxes, rules, filters, activity

        var id: Self { self }

        var title: String {
            switch self {
            case .senders: String(localized: "Senders")
            case .inboxes: String(localized: "Inboxes")
            case .rules: String(localized: "Rules")
            case .filters: String(localized: "Filters")
            case .activity: String(localized: "Activity")
            }
        }
    }

    @Bindable var model: CurateModel
    /// An account's name as the window shows it.
    let nameOf: (String) -> String
    let open: (String) -> Void
    @State var part: Part = .senders
    /// The window's mailbox, whose toast says on the sheet what was done there (`DeskSheetToast`).
    var mailbox: Mailbox?
    @Environment(\.dismiss) private var dismiss
    @Environment(\.meadow) private var meadow
    /// The sheet's own, which its Edit ▸ Undo asks while it is in front.
    @Environment(\.undoManager) private var undoManager

    var body: some View {
        VStack(spacing: 0) {
            DeskSheetHead(title: String(localized: "Curate"), icon: .sliders) {
                Spacer(minLength: 8)
                MeadowSegmentedChoice(label: String(localized: "Part"), selection: $part,
                                      options: Part.allCases.map { ($0, $0.title) }, fills: false)
            }
            Group {
                switch part {
                case .senders: FloodPage(model: model)
                case .inboxes: InboxesPage(model: model, nameOf: nameOf)
                case .rules: RulesPage(model: model)
                case .filters: FiltersPage(model: model)
                case .activity: ActivityPage(model: model, nameOf: nameOf, open: { account in dismiss(); open(account) })
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            .overlay(alignment: .bottom) {
                DeskSheetToast(mailbox: mailbox)
                    .padding(.bottom, 16)
                    .animation(.snappy(duration: 0.2), value: mailbox?.toast)
            }
            DeskSheetFoot(words: "", done: { dismiss() })
        }
        .frame(width: 620, height: 600)
        .background(meadow.paper)
        .task { await model.load() }
        .onAppear { model.sheetUndo = undoManager }
        .onChange(of: undoManager) { _, now in model.sheetUndo = now }
        .sheet(item: Binding(get: { model.cleaning.map(Asked.init) }, set: { if $0 == nil { model.cleaning = nil } })) { _ in
            CleanupSheet(model: model)
        }
        .sheet(item: Binding(get: { model.batching.map(Asked.init) }, set: { if $0 == nil { model.batching = nil } })) { _ in
            BatchSheet(model: model)
        }
        .sheet(item: $model.filterForm) { form in
            FilterSheet(form: form, model: model)
        }
    }
}

/// A preview being asked about, as a sheet's item: there is one at a time.
private struct Asked<T>: Identifiable {
    let value: T
    var id: Int { 0 }

    init(_ value: T) {
        self.value = value
    }
}

/// Measuring, while a page's numbers are read.
private struct Measuring: View {
    let words: LocalizedStringKey
    @Environment(\.meadow) private var meadow

    var body: some View {
        HStack(spacing: 8) {
            ProgressView().controlSize(.small)
            Text(words).font(.meadow(.ui, 13.5)).foregroundStyle(meadow.ink3)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

// MARK: Senders flooding you

private struct FloodPage: View {
    let model: CurateModel
    @Environment(\.meadow) private var meadow

    var body: some View {
        if model.floodFailed {
            DeskEmpty(icon: .alert, title: String(localized: "Couldn’t load."))
        } else if let flood = model.flood {
            if flood.senders.isEmpty {
                DeskEmpty(icon: .checkCircle, title: String(localized: "No sender is flooding you right now."))
            } else {
                VStack(spacing: 0) {
                    if !model.marked.isEmpty {
                        HStack(spacing: 8) {
                            Text("\(model.marked.count) marked")
                                .font(.meadow(.ui, 13.5, .bold))
                                .foregroundStyle(meadow.ink)
                            Spacer()
                            Button("Clear") { model.marked.removeAll() }.meadowButton(.quiet, small: true)
                            Button("Clean Up \(model.marked.count)") { Task { await model.previewBatch() } }
                                .meadowButton(.primary, small: true)
                        }
                        .padding(.horizontal, 16)
                        .padding(.vertical, 8)
                        .background(meadow.paper2)
                    }
                    ScrollView {
                        LazyVStack(spacing: 2) {
                            ForEach(flood.senders, id: \.fromAddr) { sender in
                                FloodRow(sender: sender, model: model)
                            }
                        }
                        .padding(10)
                    }
                    .scrollContentBackground(.hidden)
                }
            }
        } else {
            Measuring(words: "Measuring…")
        }
    }
}

private struct FloodRow: View {
    let sender: API.NoisySender
    let model: CurateModel
    @Environment(\.meadow) private var meadow

    var body: some View {
        let marked = model.marked.contains(sender.fromAddr)
        DeskSheetRow {
            DeskCheckbox(on: marked, label: String(localized: "Mark \(sender.fromAddr)")) {
                if marked { model.marked.remove(sender.fromAddr) } else { model.marked.insert(sender.fromAddr) }
            }
            VStack(alignment: .leading, spacing: 3) {
                Text(sender.fromName.isEmpty ? sender.fromAddr : sender.fromName)
                    .font(.meadow(.ui, 14, .bold))
                    .foregroundStyle(meadow.ink)
                Text(sender.fromAddr).font(.meadow(.ui, 12.5)).foregroundStyle(meadow.ink3)
                HStack(spacing: 4) {
                    MeadowChip(countNoun(sender.count, "msg", "msgs"))
                    if sender.unseen > 0 {
                        MeadowChip(String(localized: "\(sender.unseen) unread"),
                                   tone: sender.unseenRatio >= 0.7 ? .sun : .plain)
                    }
                    if sender.accounts.count > 1 { MeadowChip(String(localized: "\(sender.accounts.count) inboxes")) }
                    if let chip = model.unsubscribeChip(sender) { MeadowChip(chip) }
                    if !sender.lastSeen.isEmpty { MeadowChip(sender.lastSeen, tone: .line) }
                }
                .padding(.top, 3)
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 6) {
                Text(sender.noiseScore, format: .number.precision(.fractionLength(0...1)))
                    .font(.meadow(.ui, 12))
                    .monospacedDigit()
                    .foregroundStyle(meadow.ink3)
                    .help("noise score")
                Button("Clean Up") { Task { await model.previewCleanup(sender.fromAddr) } }
                    .meadowButton(small: true)
                    .accessibilityLabel("Clean up \(sender.fromAddr)")
            }
        }
    }
}

/// One sender's cleanup: block (this sender or the whole domain), sweep their inbox mail to Trash, and unsubscribe
/// where the mail says how. The block and the sweep undo; an unsubscribe can't.
struct CleanupSheet: View {
    let model: CurateModel
    @Environment(\.meadow) private var meadow

    var body: some View {
        if let asked = model.cleaning {
            let preview = asked.preview
            VStack(alignment: .leading, spacing: 10) {
                Text("Clean up **\(preview.fromAddr)**?")
                    .font(.meadow(.display, 19, .bold))
                    .foregroundStyle(meadow.ink)
                Text("\(countNoun(preview.count, "message", "messages")) in your synced mail\(preview.accounts.count > 1 ? " · \(preview.accounts.count) inboxes" : "")")
                    .font(.meadow(.ui, 13.5))
                    .foregroundStyle(meadow.ink3)
                Step(icon: .check, tag: String(localized: "reversible")) {
                    Text("Block **\(asked.scope == .domain ? preview.domain : preview.fromAddr)** — future mail auto-filed")
                }
                MeadowSegmentedChoice(label: String(localized: "Block"),
                                      selection: Binding(get: { asked.scope }, set: { scope in
                                          model.cleaning?.scope = scope
                                          model.cleaning?.armed = false
                                      }),
                                      options: [(API.BlockScope.sender, String(localized: "this sender")),
                                                (API.BlockScope.domain, String(localized: "whole domain"))],
                                      fills: false)
                    .padding(.leading, 26)
                Step(icon: .check, tag: String(localized: "reversible")) {
                    Text("Move **\(preview.sweepCount)** inbox \(preview.sweepCount == 1 ? "message" : "messages")\(asked.scope == .domain ? " from \(preview.fromAddr)" : "") to Trash")
                }
                switch asked.unsubscribeLine {
                case .none:
                    Step(icon: .minus, tag: nil) {
                        Text("No List-Unsubscribe found — block + sweep only").foregroundStyle(meadow.ink3)
                    }
                case .sends:
                    // Only what will go is marked as what Undo can't take back.
                    Step(icon: .alert, tag: String(localized: "can’t undo")) { Self.unsubscribeWords(preview) }
                case .opensPage:
                    Step(icon: .info, tag: nil) { Self.unsubscribeWords(preview) }
                case .notAsked:
                    Step(icon: .minus, tag: nil) {
                        Text("Not unsubscribing — block + sweep only").foregroundStyle(meadow.ink3)
                    }
                }
                if asked.method != "none" {
                    Toggle(asked.method == "http-page" ? "open the unsubscribe page" : "also send the unsubscribe now",
                           isOn: Binding(get: { asked.unsubscribe }, set: { model.chooseUnsubscribe($0) }))
                        .toggleStyle(.checkbox)
                        .font(.meadow(.ui, 13.5))
                        .padding(.leading, 26)
                }
                if preview.sweepCapped {
                    Step(icon: .info, tag: nil) {
                        Text("Sweeps the most recent \(preview.sweepCount) — run again for the rest.")
                    }
                }
                // What Undo takes back in the plain ink, and only an unsubscribe that will go in the warning's.
                (Text(CurateModel.Cleaning.undoes).foregroundStyle(meadow.ink2)
                    + Text(asked.sendsUnsubscribe ? " " + CurateModel.Cleaning.cantUndo : "").foregroundStyle(meadow.rust))
                    .font(.meadow(.ui, 13, .semibold))
                HStack(spacing: 8) {
                    Spacer()
                    Button("Cancel", role: .cancel) { model.cleaning = nil }
                        .meadowButton(.quiet)
                        .keyboardShortcut(.cancelAction)
                    Button(asked.armed ? "Click Again to Confirm" : "Clean Up", role: .destructive) {
                        Task { await model.pressCleanup() }
                    }
                    .meadowButton(asked.armed ? .danger : .standard, del: !asked.armed)
                    .keyboardShortcut(.defaultAction)
                }
                .padding(.top, 4)
            }
            .padding(20)
            .frame(width: 500)
            .background(meadow.paper)
        }
    }

    @ViewBuilder static func unsubscribeWords(_ preview: API.SenderPreview) -> some View {
        switch preview.unsub.method {
        case "one-click":
            Text("Send a **one-click** unsubscribe to \(preview.unsub.httpsHost ?? String(localized: "the sender")) — no email, no browser")
        case "mailto":
            Text("Email the list’s unsubscribe address **\(preview.unsub.mailtoTo ?? "")** from your account")
        default:
            Text("Open the unsubscribe **page** in your browser (nothing is auto-submitted)")
        }
    }
}

/// A line of what a cleanup does, and whether it can be undone.
private struct Step<Words: View>: View {
    let icon: MeadowIcon
    let tag: String?
    @ViewBuilder let words: Words
    @Environment(\.meadow) private var meadow

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            MeadowIconView(icon, size: 16)
                .foregroundStyle(meadow.ink2)
                .frame(width: 16)
                .alignmentGuide(.firstTextBaseline) { $0[VerticalAlignment.center] + 5 }
            words
            Spacer(minLength: 4)
            if let tag { MeadowChip(tag, tone: .line) }
        }
        .font(.meadow(.ui, 13.5))
        .foregroundStyle(meadow.ink)
    }
}

/// Several senders cleaned up at once: each blocked and swept, and one Undo takes it all back. It doesn't
/// unsubscribe: that goes one sender at a time.
struct BatchSheet: View {
    let model: CurateModel
    @Environment(\.meadow) private var meadow

    var body: some View {
        if let asked = model.batching {
            let preview = asked.preview
            VStack(alignment: .leading, spacing: 10) {
                Text("Clean up **\(countNoun(preview.senders.count, "sender", "senders"))**?")
                    .font(.meadow(.display, 19, .bold))
                    .foregroundStyle(meadow.ink)
                Text(asked.reasoning ? String(localized: "Writing why each one is here…")
                     : String(localized: "\(countNoun(preview.sweepCount, "inbox message", "inbox messages")) go to Trash"))
                    .font(.meadow(.ui, 13.5))
                    .foregroundStyle(meadow.ink3)
                ScrollView {
                    VStack(alignment: .leading, spacing: 10) {
                        ForEach(preview.senders, id: \.fromAddr) { sender in
                            VStack(alignment: .leading, spacing: 2) {
                                Text(sender.fromAddr).font(.meadow(.ui, 14, .bold)).foregroundStyle(meadow.ink)
                                Text("\(countNoun(sender.count, "message", "messages"))\(sender.unseen > 0 ? " · \(sender.unseen) unread" : "") · \(sender.sweepCount) to Trash")
                                    .font(.meadow(.ui, 12.5))
                                    .foregroundStyle(meadow.ink3)
                                if let why = asked.reasons[sender.fromAddr] {
                                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                                        MeadowIconView(.wind, size: 13)
                                        Text(why)
                                    }
                                    .font(.meadow(.ui, 12.5, .medium))
                                    .foregroundStyle(meadow.moss)
                                }
                            }
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(maxHeight: 280)
                if preview.sweepCapped {
                    Step(icon: .info, tag: nil) {
                        Text("Sweeps the most recent \(preview.sweepCount) — run again for the rest.")
                    }
                }
                Text("Each one is blocked and swept, and one Undo takes it all back. This doesn’t unsubscribe: do that one sender at a time.")
                    .font(.meadow(.ui, 13, .semibold))
                    .foregroundStyle(meadow.rust)
                HStack(spacing: 8) {
                    Spacer()
                    Button("Cancel", role: .cancel) { model.batching = nil }
                        .meadowButton(.quiet)
                        .keyboardShortcut(.cancelAction)
                    Button(asked.armed ? "Click Again to Confirm" : "Clean Up \(preview.senders.count)", role: .destructive) {
                        Task { await model.pressBatch() }
                    }
                    .meadowButton(asked.armed ? .danger : .standard, del: !asked.armed)
                    .keyboardShortcut(.defaultAction)
                }
            }
            .padding(20)
            .frame(width: 520)
            .background(meadow.paper)
        }
    }
}

// MARK: Inboxes

private struct InboxesPage: View {
    let model: CurateModel
    let nameOf: (String) -> String

    var body: some View {
        if let stats = model.stats {
            ScrollView {
                LazyVStack(spacing: 10) {
                    ForEach(stats.accounts, id: \.account) { account in
                        InboxRow(account: account, buckets: stats.buckets, model: model, nameOf: nameOf)
                    }
                }
                .padding(16)
            }
            .scrollContentBackground(.hidden)
        } else {
            Measuring(words: "Measuring live inboxes…")
        }
    }
}

private struct InboxRow: View {
    let account: API.AccountStats
    let buckets: [API.Bucket]
    let model: CurateModel
    let nameOf: (String) -> String
    @Environment(\.meadow) private var meadow

    var body: some View {
        let total = account.inInbox
        DeskCard {
            HStack(spacing: 8) {
                Circle().fill(account.error == nil ? meadow.primary : meadow.rust).frame(width: 8, height: 8)
                Text(nameOf(account.account)).font(.meadow(.ui, 14, .bold)).foregroundStyle(meadow.ink)
                Spacer()
                Text(account.error == nil ? "\(total)" : "—")
                    .font(.meadow(.display, 18, .bold))
                    .monospacedDigit()
                    .foregroundStyle(meadow.ink)
            }
            GeometryReader { space in
                HStack(spacing: 0) {
                    ForEach(buckets, id: \.name) { bucket in
                        if let count = account.byBucket[bucket.name], count > 0, total > 0 {
                            Rectangle()
                                .fill(Self.colour(bucket.name, meadow))
                                .frame(width: space.size.width * CGFloat(count) / CGFloat(total))
                                .help("\(bucket.name): \(count)")
                        }
                    }
                }
            }
            .frame(height: 6)
            .background(meadow.line)
            .clipShape(Capsule())
            HStack {
                if let error = account.error {
                    Text(String(error.prefix(40))).foregroundStyle(meadow.rust)
                } else {
                    Text("\(account.junk) junk · \(account.keep) keep").foregroundStyle(meadow.ink3)
                }
                Spacer()
                if let offer = CurateModel.sweepOffer(account), model.sweepArmed?.account != account.account,
                   model.sweepBusy?.account != account.account {
                    // One sweep at a time: its plan reads the whole Inbox from the server.
                    Button("Sweep \(offer)") { Task { await model.armSweep(account.account) } }
                        .meadowButton(small: true)
                        .disabled(model.sweepBusy != nil)
                }
            }
            .font(.meadow(.ui, 13))
            if let busy = model.sweepBusy, busy.account == account.account {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text(busy.words).font(.meadow(.ui, 13.5)).foregroundStyle(meadow.sunInk)
                    Spacer()
                }
                .padding(10)
                .background(RoundedRectangle(cornerRadius: 10).fill(meadow.sunWash))
                .accessibilityElement(children: .combine)
            }
            if let armed = model.sweepArmed, armed.account == account.account {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Sweep **\(nameOf(account.account))** · **\(armed.planned)** to Trash")
                        .font(.meadow(.ui, 13.5))
                        .foregroundStyle(meadow.sunInk)
                    ForEach(armed.going ?? [], id: \.bucket) { bucket in
                        SweepBucketLines(bucket: bucket)
                    }
                    ForEach(CurateModel.sweepNotes(armed), id: \.self) { note in
                        Text(note).font(.meadow(.ui, 12.5, .semibold)).foregroundStyle(meadow.sunInk)
                    }
                    HStack(spacing: 8) {
                        Text(CurateModel.undoLine(armed)).font(.meadow(.ui, 12.5)).foregroundStyle(meadow.ink3)
                        Spacer()
                        Button("Abort") { model.abortSweep() }.meadowButton(.quiet, small: true)
                        Button("Confirm") { Task { await model.sweep() } }.meadowButton(.danger, small: true)
                            .disabled(model.sweepBusy != nil)
                    }
                }
                .padding(10)
                .background(RoundedRectangle(cornerRadius: 10).fill(meadow.sunWash))
            }
        }
    }

    /// Each bucket's colour, as the page's microbar draws it, in Meadow's palette: rust for what is junk, sunlight
    /// for what sells, indigo for people and their receipts, and the quiet ink for the machines. Moss stays the AI's.
    static func colour(_ bucket: String, _ meadow: MeadowPalette) -> Color {
        switch bucket {
        case "spam/scam": meadow.rust
        case "blocked": meadow.rust.opacity(0.55)
        case "newsletters/marketing": meadow.sun
        case "automated-notifications": meadow.ink3.opacity(0.7)
        case "receipts/transactional": meadow.primary.opacity(0.55)
        case "personal/protected": meadow.primary
        case "other/personal-ish": meadow.sun.opacity(0.5)
        default: meadow.ink3
        }
    }
}

/// One bucket a sweep would move: its name and count, the senders with the most of it, and its newest subjects.
private struct SweepBucketLines: View {
    let bucket: API.SweepBucket
    @Environment(\.meadow) private var meadow

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 6) {
                Circle().fill(InboxRow.colour(bucket.bucket, meadow)).frame(width: 7, height: 7)
                Text(CurateModel.bucketWords(bucket.bucket)).font(.meadow(.ui, 13, .bold)).foregroundStyle(meadow.ink)
                Spacer()
                Text("\(bucket.count)").font(.meadow(.ui, 13)).monospacedDigit().foregroundStyle(meadow.ink3)
            }
            Text(CurateModel.sweepSenders(bucket))
                .font(.meadow(.mono, 11.5))
                .foregroundStyle(meadow.ink3)
                .fixedSize(horizontal: false, vertical: true)
            ForEach(Array(bucket.subjects.enumerated()), id: \.offset) { _, subject in
                Text("· " + (subject.isEmpty ? String(localized: "(no subject)") : subject))
                    .font(.meadow(.ui, 12.5))
                    .foregroundStyle(meadow.ink2)
                    .lineLimit(1)
                    .truncationMode(.tail)
            }
        }
        .padding(.top, 6)
        .overlay(alignment: .top) { Rectangle().fill(meadow.line).frame(height: 1) }
    }
}

// MARK: Learned rules

private struct RulesPage: View {
    let model: CurateModel
    @State private var text = ""
    @FocusState private var typing: Bool
    @Environment(\.meadow) private var meadow

    var body: some View {
        let rules = model.rules
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                MeadowSectionLabel(String(localized: "Always keep"))
                chips((rules?.keepDomains ?? []) + (rules?.keepSenders ?? []), keep: true)
                MeadowSectionLabel(String(localized: "Always trash"))
                    .padding(.top, 6)
                chips((rules?.blockDomains ?? []) + (rules?.blockSenders ?? []), keep: false)
                Rectangle().fill(meadow.line).frame(height: 1).padding(.vertical, 6)
                HStack(spacing: 8) {
                    TextField("domain.com or name@host", text: $text)
                        .focused($typing)
                        .deskField(focused: typing)
                        .onSubmit { add(.keep) }
                    Button("Keep") { add(.keep) }.meadowButton()
                    Button("Block") { add(.block) }.meadowButton(del: true)
                }
                .disabled(model.rules == nil)
                Text("A sender or a whole domain: keep it in your inbox always, or send it to the trash always.")
                    .font(.meadow(.ui, 12.5))
                    .foregroundStyle(meadow.ink3)
            }
            .padding(16)
        }
        .scrollContentBackground(.hidden)
    }

    /// What was typed stays until the rule is kept, so a refused one can be put right rather than typed again.
    private func add(_ verdict: API.RuleChange) {
        let token = text.trimmingCharacters(in: .whitespaces)
        guard !token.isEmpty else { return }
        Task {
            if await model.rule(token, verdict), text.trimmingCharacters(in: .whitespaces) == token { text = "" }
        }
    }

    @ViewBuilder private func chips(_ tokens: [String], keep: Bool) -> some View {
        if tokens.isEmpty {
            Text("none").font(.meadow(.ui, 12.5)).foregroundStyle(meadow.ink3)
        } else {
            BriefFlow(spacing: 6) {
                ForEach(tokens, id: \.self) { token in
                    Button {
                        Task { await model.rule(token, .remove) }
                    } label: {
                        HStack(spacing: 5) {
                            Text(token)
                            MeadowIconView(.x, size: 12)
                        }
                        .font(.meadow(.ui, 12.5, .bold))
                        .foregroundStyle(keep ? meadow.primaryInk : meadow.rust)
                        .padding(.horizontal, 10)
                        .frame(minHeight: 26)
                        .background(Capsule().fill(keep ? meadow.primaryWash : meadow.rustWash))
                        .contentShape(Capsule())
                    }
                    .buttonStyle(.plain)
                    .help("Remove this rule")
                    .accessibilityLabel("Remove the rule for \(token)")
                }
            }
        }
    }
}

// MARK: Filters

private struct FiltersPage: View {
    let model: CurateModel
    @Environment(\.meadow) private var meadow

    var body: some View {
        let filters = model.rules?.filters ?? []
        VStack(alignment: .leading, spacing: 0) {
            if filters.isEmpty {
                DeskEmpty(icon: .filter, title: String(localized: "No filters"),
                          words: String(localized: "A filter stars or marks read the new mail that fits a search."))
                Spacer(minLength: 0)
            } else {
                ScrollView {
                    LazyVStack(spacing: 2) {
                        ForEach(filters, id: \.id) { filter in
                            DeskSheetRow {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(filter.q).font(.meadow(.mono, 13)).foregroundStyle(meadow.ink)
                                    Text(CurateModel.does(filter)).font(.meadow(.ui, 12.5)).foregroundStyle(meadow.ink3)
                                }
                                Spacer()
                                Button("Edit") {
                                    model.filterForm = .init(id: filter.id, query: filter.q, star: filter.star,
                                                             read: filter.read)
                                }
                                .meadowButton(.quiet, small: true)
                                .accessibilityLabel("Edit the filter \(filter.q)")
                                Button("Remove") { Task { await model.removeFilter(filter) } }
                                    .meadowButton(.quiet, del: true, small: true)
                                    .accessibilityLabel("Remove the filter \(filter.q)")
                            }
                        }
                    }
                    .padding(10)
                }
                .scrollContentBackground(.hidden)
            }
            HStack {
                Button { model.filterForm = .new() } label: {
                    MeadowIconView(.plus, size: 15)
                    Text("New Filter…")
                }
                .meadowButton(small: true)
                Spacer()
            }
            .padding(12)
        }
    }
}

/// What a person asks of mail as it arrives: a search, and whether to star what fits it or mark it read. The server
/// says what a filter can't look at, in words the sheet shows.
struct FilterSheet: View {
    let model: CurateModel
    @Environment(\.dismiss) private var dismiss
    @Environment(\.meadow) private var meadow
    @State private var form: CurateModel.FilterForm
    @State private var why = ""
    @State private var saving = false
    @FocusState private var typing: Bool

    init(form: CurateModel.FilterForm, model: CurateModel) {
        self.model = model
        _form = State(initialValue: form)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                MeadowIconView(.filter, size: 18).foregroundStyle(meadow.ink2)
                Text(form.id == nil ? "New filter" : "Edit filter")
                    .font(.meadow(.display, 19, .bold))
                    .foregroundStyle(meadow.ink)
            }
            Text("New mail that fits this search").font(.meadow(.ui, 13.5)).foregroundStyle(meadow.ink2)
            TextField("from:name@host subject:invoice", text: $form.query)
                .focused($typing)
                .deskField(focused: typing)
                .autocorrectionDisabled()
            Toggle("Star it", isOn: $form.star).toggleStyle(.checkbox).font(.meadow(.ui, 13.5))
            Toggle("Mark it read", isOn: $form.read).toggleStyle(.checkbox).font(.meadow(.ui, 13.5))
            Text("Applies to new mail arriving in your Inbox, not to what’s already here. Your mail server hears of it too. A filter never moves mail.")
                .font(.meadow(.ui, 12.5))
                .foregroundStyle(meadow.ink3)
            if !why.isEmpty {
                Text(why).font(.meadow(.ui, 13)).foregroundStyle(meadow.rust)
            }
            HStack(spacing: 8) {
                Spacer()
                Button("Cancel", role: .cancel) {
                    model.filterForm = nil
                    dismiss()
                }
                .meadowButton(.quiet)
                .keyboardShortcut(.cancelAction)
                Button(form.id == nil ? "Create Filter" : "Save") { Task { await save() } }
                    .meadowButton(.primary)
                    .keyboardShortcut(.defaultAction)
                    .disabled(saving)
            }
        }
        .padding(20)
        .frame(width: 460)
        .background(meadow.paper)
    }

    private func save() async {
        saving = true
        why = ""
        do {
            try await model.save(form)
            dismiss()
        } catch {
            why = DeskRefusal.words(error, else: String(localized: "Couldn’t save the filter."))
            saving = false
        }
    }
}

// MARK: Activity

private struct ActivityPage: View {
    let model: CurateModel
    let nameOf: (String) -> String
    let open: (String) -> Void
    @Environment(\.meadow) private var meadow

    var body: some View {
        if model.audit.isEmpty {
            DeskEmpty(icon: .clock, title: String(localized: "No activity yet."))
        } else {
            ScrollView {
                LazyVStack(spacing: 0) {
                    ForEach(Array(model.audit.enumerated()), id: \.offset) { _, event in
                        Button {
                            if let account = event.account { open(account) }
                        } label: {
                            DeskSheetRow {
                                Text(event.kind)
                                    .font(.meadow(.mono, 12, .bold))
                                    .foregroundStyle(meadow.ink2)
                                    .frame(width: 84, alignment: .leading)
                                Text(Self.when(event.ts))
                                    .font(.meadow(.ui, 12.5))
                                    .monospacedDigit()
                                    .foregroundStyle(meadow.ink3)
                                    .frame(width: 110, alignment: .leading)
                                Text(Self.line(event, nameOf))
                                    .font(.meadow(.ui, 13))
                                    .foregroundStyle(meadow.ink)
                                    .lineLimit(1)
                                Spacer()
                            }
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(10)
            }
            .scrollContentBackground(.hidden)
        }
    }

    static func when(_ ts: String) -> String {
        guard let date = EventWhen.moment(ts) else { return "" }
        return date.formatted(.dateTime.month(.abbreviated).day().hour().minute())
    }

    static func line(_ event: API.AuditEvent, _ nameOf: (String) -> String) -> String {
        let who = nameOf(event.account ?? "")
        if event.kind == "reconcile" { return "struck \(event.pruned ?? 0) · \(who)" }
        return "\(who) · \(String((event.from ?? "").prefix(22)))"
    }
}
#endif
