#if os(macOS)
import SwiftUI

/// Curate: the page's drawer for looking after the mailbox, as a sheet with a page for each of its parts.
struct CurateSheet: View {
    enum Part: String, CaseIterable, Identifiable {
        case senders, inboxes, rules, filters, activity

        var id: Self { self }

        var title: LocalizedStringKey {
            switch self {
            case .senders: "Senders"
            case .inboxes: "Inboxes"
            case .rules: "Rules"
            case .filters: "Filters"
            case .activity: "Activity"
            }
        }
    }

    @Bindable var model: CurateModel
    /// An account's name as the window shows it.
    let nameOf: (String) -> String
    let open: (String) -> Void
    @State var part: Part = .senders
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(spacing: 0) {
            Picker("Part", selection: $part) {
                ForEach(Part.allCases) { Text($0.title).tag($0) }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .padding(12)
            Divider()
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
            Divider()
            HStack {
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.cancelAction)
            }
            .padding(12)
        }
        .frame(width: 620, height: 600)
        .task { await model.load() }
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

// MARK: Senders flooding you

private struct FloodPage: View {
    let model: CurateModel

    var body: some View {
        if model.floodFailed {
            ContentUnavailableView("Couldn’t load.", systemImage: "exclamationmark.triangle")
        } else if let flood = model.flood {
            if flood.senders.isEmpty {
                ContentUnavailableView("No sender is flooding you right now.", systemImage: "checkmark.circle")
            } else {
                VStack(spacing: 0) {
                    if !model.marked.isEmpty {
                        HStack {
                            Text("\(model.marked.count) marked")
                            Spacer()
                            Button("Clear") { model.marked.removeAll() }
                            Button("Clean Up \(model.marked.count)") { Task { await model.previewBatch() } }
                                .buttonStyle(.borderedProminent)
                        }
                        .padding(.horizontal, 16)
                        .padding(.vertical, 8)
                        .background(.bar)
                    }
                    List(flood.senders, id: \.fromAddr) { sender in
                        FloodRow(sender: sender, model: model)
                    }
                    .listStyle(.inset)
                }
            }
        } else {
            ProgressView("Measuring…").frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }
}

private struct FloodRow: View {
    let sender: API.NoisySender
    let model: CurateModel

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Toggle("Mark \(sender.fromAddr)", isOn: Binding(
                get: { model.marked.contains(sender.fromAddr) },
                set: { on in if on { model.marked.insert(sender.fromAddr) } else { model.marked.remove(sender.fromAddr) } }))
                .labelsHidden()
                .toggleStyle(.checkbox)
            VStack(alignment: .leading, spacing: 3) {
                Text(sender.fromName.isEmpty ? sender.fromAddr : sender.fromName).fontWeight(.semibold)
                Text(sender.fromAddr).font(.caption).foregroundStyle(.secondary)
                HStack(spacing: 4) {
                    CardTag(LocalizedStringKey(countNoun(sender.count, "msg", "msgs")))
                    if sender.unseen > 0 {
                        Text("\(sender.unseen) unread")
                            .font(.caption2.weight(.medium))
                            .padding(.horizontal, 6)
                            .padding(.vertical, 1)
                            .background(sender.unseenRatio >= 0.7 ? AnyShapeStyle(Color.orange.opacity(0.3)) : AnyShapeStyle(.quaternary),
                                        in: Capsule())
                    }
                    if sender.accounts.count > 1 { CardTag("\(sender.accounts.count) inboxes") }
                    if sender.luLikely { CardTag("1-click") }
                    if !sender.lastSeen.isEmpty { CardTag(LocalizedStringKey(sender.lastSeen)) }
                }
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 4) {
                Text(sender.noiseScore, format: .number.precision(.fractionLength(0...1)))
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
                    .help("noise score")
                Button("Clean Up") { Task { await model.previewCleanup(sender.fromAddr) } }
                    .controlSize(.small)
                    .accessibilityLabel("Clean up \(sender.fromAddr)")
            }
        }
        .padding(.vertical, 4)
    }
}

/// One sender's cleanup: block (this sender or the whole domain), sweep their inbox mail to Trash, and unsubscribe
/// where the mail says how. The block and the sweep undo; an unsubscribe can't.
struct CleanupSheet: View {
    let model: CurateModel

    var body: some View {
        if let asked = model.cleaning {
            let preview = asked.preview
            VStack(alignment: .leading, spacing: 10) {
                Text("Clean up **\(preview.fromAddr)**?").font(.headline)
                Text("\(countNoun(preview.count, "message", "messages")) in your synced mail\(preview.accounts.count > 1 ? " · \(preview.accounts.count) inboxes" : "")")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                Step(symbol: "checkmark", tag: "reversible") {
                    Text("Block **\(asked.scope == .domain ? preview.domain : preview.fromAddr)** — future mail auto-filed")
                }
                Picker("Block", selection: Binding(get: { asked.scope }, set: { scope in
                    model.cleaning?.scope = scope
                    model.cleaning?.armed = false
                })) {
                    Text("this sender").tag(API.BlockScope.sender)
                    Text("whole domain").tag(API.BlockScope.domain)
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .frame(width: 240)
                Step(symbol: "checkmark", tag: "reversible") {
                    Text("Move **\(preview.sweepCount)** inbox \(preview.sweepCount == 1 ? "message" : "messages")\(asked.scope == .domain ? " from \(preview.fromAddr)" : "") to Trash")
                }
                if asked.method == "none" {
                    Step(symbol: "minus", tag: nil) {
                        Text("No List-Unsubscribe found — block + sweep only").foregroundStyle(.secondary)
                    }
                } else {
                    Step(symbol: "exclamationmark.triangle", tag: "can’t undo") { Self.unsubscribeWords(preview) }
                    Toggle(asked.method == "http-page" ? "open the unsubscribe page" : "also send the unsubscribe now",
                           isOn: Binding(get: { asked.unsubscribe }, set: { model.cleaning?.unsubscribe = $0 }))
                        .toggleStyle(.checkbox)
                }
                if preview.sweepCapped {
                    Label("Sweeps the most recent \(preview.sweepCount) — run again for the rest.", systemImage: "exclamationmark.circle")
                        .font(.callout)
                }
                Text("Block + sweep undo with Undo.\(asked.method == "none" ? "" : " Unsubscribing can’t be undone.")")
                    .font(.callout)
                    .foregroundStyle(.red)
                HStack {
                    Spacer()
                    Button("Cancel", role: .cancel) { model.cleaning = nil }.keyboardShortcut(.cancelAction)
                    Button(asked.armed ? "Click Again to Confirm" : "Clean Up", role: .destructive) {
                        Task { await model.pressCleanup() }
                    }
                    .keyboardShortcut(.defaultAction)
                    .tint(.red)
                }
                .padding(.top, 4)
            }
            .padding(20)
            .frame(width: 500)
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
    let symbol: String
    let tag: LocalizedStringKey?
    @ViewBuilder let words: Words

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Image(systemName: symbol).frame(width: 16)
            words
            Spacer(minLength: 4)
            if let tag { CardTag(tag) }
        }
        .font(.callout)
    }
}

/// Several senders cleaned up at once: each blocked and swept, and one Undo takes it all back. It doesn't
/// unsubscribe: that goes one sender at a time.
struct BatchSheet: View {
    let model: CurateModel

    var body: some View {
        if let asked = model.batching {
            let preview = asked.preview
            VStack(alignment: .leading, spacing: 10) {
                Text("Clean up **\(countNoun(preview.senders.count, "sender", "senders"))**?").font(.headline)
                Text(asked.reasoning ? String(localized: "Writing why each one is here…")
                     : String(localized: "\(countNoun(preview.sweepCount, "inbox message", "inbox messages")) go to Trash"))
                    .font(.callout)
                    .foregroundStyle(.secondary)
                ScrollView {
                    VStack(alignment: .leading, spacing: 8) {
                        ForEach(preview.senders, id: \.fromAddr) { sender in
                            VStack(alignment: .leading, spacing: 2) {
                                Text(sender.fromAddr).fontWeight(.semibold)
                                Text("\(countNoun(sender.count, "message", "messages"))\(sender.unseen > 0 ? " · \(sender.unseen) unread" : "") · \(sender.sweepCount) to Trash")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                if let why = asked.reasons[sender.fromAddr] {
                                    Label(why, systemImage: "sparkles").font(.caption).foregroundStyle(.tint)
                                }
                            }
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(maxHeight: 280)
                if preview.sweepCapped {
                    Label("Sweeps the most recent \(preview.sweepCount) — run again for the rest.", systemImage: "exclamationmark.circle")
                        .font(.callout)
                }
                Text("Each one is blocked and swept, and one Undo takes it all back. This doesn’t unsubscribe: do that one sender at a time.")
                    .font(.callout)
                    .foregroundStyle(.red)
                HStack {
                    Spacer()
                    Button("Cancel", role: .cancel) { model.batching = nil }.keyboardShortcut(.cancelAction)
                    Button(asked.armed ? "Click Again to Confirm" : "Clean Up \(preview.senders.count)", role: .destructive) {
                        Task { await model.pressBatch() }
                    }
                    .keyboardShortcut(.defaultAction)
                    .tint(.red)
                }
            }
            .padding(20)
            .frame(width: 520)
        }
    }
}

// MARK: Inboxes

private struct InboxesPage: View {
    let model: CurateModel
    let nameOf: (String) -> String

    var body: some View {
        if let stats = model.stats {
            List(stats.accounts, id: \.account) { account in
                InboxRow(account: account, buckets: stats.buckets, model: model, nameOf: nameOf)
            }
            .listStyle(.inset)
        } else {
            ProgressView("Measuring live inboxes…").frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }
}

private struct InboxRow: View {
    let account: API.AccountStats
    let buckets: [API.Bucket]
    let model: CurateModel
    let nameOf: (String) -> String

    var body: some View {
        let total = account.inInbox
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Circle().fill(account.error == nil ? Color.green : Color.red).frame(width: 8, height: 8)
                Text(nameOf(account.account)).fontWeight(.semibold)
                Spacer()
                Text(account.error == nil ? "\(total)" : "—").monospacedDigit()
            }
            GeometryReader { space in
                HStack(spacing: 0) {
                    ForEach(buckets, id: \.name) { bucket in
                        if let count = account.byBucket[bucket.name], count > 0, total > 0 {
                            Rectangle()
                                .fill(Self.colour(bucket.name))
                                .frame(width: space.size.width * CGFloat(count) / CGFloat(total))
                                .help("\(bucket.name): \(count)")
                        }
                    }
                }
            }
            .frame(height: 6)
            .background(.quaternary)
            .clipShape(Capsule())
            HStack {
                if let error = account.error {
                    Text(String(error.prefix(40))).foregroundStyle(.red)
                } else {
                    Text("\(account.junk) junk · \(account.keep) keep").foregroundStyle(.secondary)
                }
                Spacer()
                if account.junk > 0, model.sweepArmed?.account != account.account {
                    Button("Sweep \(account.junk)") { Task { await model.armSweep(account.account) } }
                        .controlSize(.small)
                }
            }
            .font(.callout)
            if let armed = model.sweepArmed, armed.account == account.account {
                HStack {
                    Text("Sweep **\(nameOf(account.account))** · **\(armed.planned)** msgs")
                    Spacer()
                    Button("Abort") { model.abortSweep() }
                    Button("Confirm") { Task { await model.sweep() } }
                        .buttonStyle(.borderedProminent)
                }
                .controlSize(.small)
                .padding(8)
                .background(Color.orange.opacity(0.15), in: RoundedRectangle(cornerRadius: 6))
            }
        }
        .padding(.vertical, 6)
    }

    /// Each bucket's colour, as the page's microbar draws it.
    static func colour(_ bucket: String) -> Color {
        switch bucket {
        case "spam/scam": .red
        case "blocked": .pink
        case "newsletters/marketing": .purple
        case "automated-notifications": .blue
        case "receipts/transactional": .teal
        case "personal/protected": .green
        case "other/personal-ish": .yellow
        default: .gray
        }
    }
}

// MARK: Learned rules

private struct RulesPage: View {
    let model: CurateModel
    @State private var text = ""

    var body: some View {
        let rules = model.rules
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                Text("Always keep").font(.callout.weight(.semibold))
                chips((rules?.keepDomains ?? []) + (rules?.keepSenders ?? []), tint: .green)
                Text("Always trash").font(.callout.weight(.semibold))
                chips((rules?.blockDomains ?? []) + (rules?.blockSenders ?? []), tint: .red)
                Divider()
                HStack {
                    TextField("domain.com or name@host", text: $text)
                        .textFieldStyle(.roundedBorder)
                        .onSubmit { add(.keep) }
                    Button("Keep") { add(.keep) }
                    Button("Block") { add(.block) }
                }
                .disabled(model.rules == nil)
                Text("A sender or a whole domain: keep it in your inbox always, or send it to the trash always.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .padding(16)
        }
    }

    private func add(_ verdict: API.RuleChange) {
        let token = text.trimmingCharacters(in: .whitespaces)
        guard !token.isEmpty else { return }
        text = ""
        Task { await model.rule(token, verdict) }
    }

    @ViewBuilder private func chips(_ tokens: [String], tint: Color) -> some View {
        if tokens.isEmpty {
            Text("none").font(.caption).foregroundStyle(.tertiary)
        } else {
            BriefFlow {
                ForEach(tokens, id: \.self) { token in
                    Button {
                        Task { await model.rule(token, .remove) }
                    } label: {
                        HStack(spacing: 4) {
                            Text(token)
                            Image(systemName: "xmark").font(.caption2)
                        }
                        .font(.callout)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 3)
                        .background(tint.opacity(0.15), in: Capsule())
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

    var body: some View {
        let filters = model.rules?.filters ?? []
        VStack(alignment: .leading, spacing: 0) {
            if filters.isEmpty {
                ContentUnavailableView("No filters", systemImage: "line.3.horizontal.decrease.circle",
                                       description: Text("A filter stars or marks read the new mail that fits a search."))
            } else {
                List(filters, id: \.id) { filter in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(filter.q).font(.body.monospaced())
                            Text(CurateModel.does(filter)).font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button("Edit") {
                            model.filterForm = .init(id: filter.id, query: filter.q, star: filter.star, read: filter.read)
                        }
                        .accessibilityLabel("Edit the filter \(filter.q)")
                        Button("Remove") { Task { await model.removeFilter(filter) } }
                            .accessibilityLabel("Remove the filter \(filter.q)")
                    }
                    .controlSize(.small)
                }
                .listStyle(.inset)
            }
            HStack {
                Button("New Filter…", systemImage: "plus") { model.filterForm = .new() }
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
    @State private var form: CurateModel.FilterForm
    @State private var why = ""
    @State private var saving = false

    init(form: CurateModel.FilterForm, model: CurateModel) {
        self.model = model
        _form = State(initialValue: form)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(form.id == nil ? "New Filter" : "Edit Filter").font(.headline)
            Text("New mail that fits this search").font(.callout)
            TextField("from:name@host subject:invoice", text: $form.query)
                .textFieldStyle(.roundedBorder)
                .font(.body.monospaced())
                .autocorrectionDisabled()
            Toggle("Star it", isOn: $form.star).toggleStyle(.checkbox)
            Toggle("Mark it read", isOn: $form.read).toggleStyle(.checkbox)
            Text("Applies to new mail arriving in your Inbox, not to what’s already here. Your mail server hears of it too. A filter never moves mail.")
                .font(.caption)
                .foregroundStyle(.secondary)
            if !why.isEmpty {
                Text(why).font(.callout).foregroundStyle(.red)
            }
            HStack {
                Spacer()
                Button("Cancel", role: .cancel) {
                    model.filterForm = nil
                    dismiss()
                }
                .keyboardShortcut(.cancelAction)
                Button(form.id == nil ? "Create Filter" : "Save") { Task { await save() } }
                    .keyboardShortcut(.defaultAction)
                    .disabled(saving)
            }
        }
        .padding(20)
        .frame(width: 460)
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

    var body: some View {
        if model.audit.isEmpty {
            ContentUnavailableView("No activity yet.", systemImage: "clock")
        } else {
            List(Array(model.audit.enumerated()), id: \.offset) { _, event in
                Button {
                    if let account = event.account { open(account) }
                } label: {
                    HStack(spacing: 10) {
                        Text(event.kind)
                            .font(.caption.monospaced().weight(.semibold))
                            .frame(width: 80, alignment: .leading)
                        Text(Self.when(event.ts)).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                            .frame(width: 110, alignment: .leading)
                        Text(Self.line(event, nameOf)).font(.caption).lineLimit(1)
                        Spacer()
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
            .listStyle(.inset)
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
