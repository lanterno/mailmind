#if os(macOS)
import CryptoKit
import Foundation
import Observation

@MainActor
@Observable
final class EncryptedMailboxModel {
    var address: String? { index.address }
    var folders: [EncryptedMailboxAPI.Folder] { index.folders }
    var draft: HostedDraft { index.draft ?? HostedDraft() }
    var hasDraft: Bool { index.draft != nil }
    var folder: String?
    var selected: String?
    var reading: HostedMailReading?
    var status: String?
    var sendStatus: String?
    private(set) var failedSubmission = false
    var syncing = false
    var sending = false
    var ready: Bool { api != nil }

    private let root = URL.applicationSupportDirectory.appending(path: "HodHodMail", directoryHint: .isDirectory)
        .appending(path: Bundle.main.bundleIdentifier ?? "hodhod", directoryHint: .isDirectory)
    private var index = EncryptedMailboxCache.Index()
    private var headers: [String: HostedMailHeader] = [:]
    private var raw: Data?
    private var api: EncryptedMailboxAPI?
    private var cache: EncryptedMailboxCache?

    var items: [(EncryptedMailboxCache.Entry, HostedMailHeader)] {
        index.entries.values.filter { $0.folder == folder }.sorted { ($0.received, $0.id) > ($1.received, $1.id) }
            .compactMap { entry in headers[entry.id].map { (entry, $0) } }
    }

    var currentHeader: HostedMailHeader? { selected.flatMap { headers[$0] } }
    var currentEntry: EncryptedMailboxCache.Entry? { selected.flatMap { index.entries[$0] } }
    var destination: EncryptedMailboxAPI.Folder? {
        let role = folders.first(where: { $0.id == folder })?.role
        switch role {
        case "inbox": return folders.first { $0.role == "archive" }
        case "archive": return folders.first { $0.role == "inbox" }
        default: return nil
        }
    }

    func start() async {
        guard !ready else { return }
        if let path = ProcessInfo.processInfo.environment["HODHOD_ENCRYPTED_PROFILE"] {
            await importProfile(URL(fileURLWithPath: path))
            return
        } else if let profile = EncryptedMailboxKeychain.load() {
            do { try connect(profile) } catch { status = message(for: error) }
        }
        await refresh()
    }

    func importProfile(_ url: URL) async {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        do {
            let profile = try EncryptedMailboxProfile.readFile(url)
            try connect(profile)
            await refresh()
            if profile.origin.hasPrefix("https://") { try EncryptedMailboxKeychain.save(profile) }
        } catch {
            status = message(for: error)
        }
    }

    private func connect(_ profile: EncryptedMailboxProfile) throws {
        let local = try EncryptedMailboxCache(profile, root: root)
        let saved = try local.load()
        var restored: [String: HostedMailHeader] = [:]
        for entry in saved.entries.values {
            guard let bytes = try local.read(entry.id) else { throw HostedMailboxIssue.corrupt }
            let raw = try HostedEnvelope.open(bytes, profile: profile, id: entry.id, keyVersion: entry.keyVersion)
            restored[entry.id] = hostedHeaders(raw: raw)
        }
        headers = restored
        index = saved
        cache = local
        api = EncryptedMailboxAPI(profile: profile)
        folder = saved.folders.first { $0.role == "inbox" }?.id
        selected = nil
        closeMessage()
    }

    func refresh() async {
        guard let api, let cache, !syncing else { return }
        syncing = true
        defer { syncing = false }
        do {
            async let address = api.account()
            async let folders = api.folders()
            let account = try await (address, folders)
            var cursor = index.cursor
            while true {
                let page = try await api.changes(after: cursor)
                var updated: [String: (EncryptedMailboxCache.Entry, HostedMailHeader)] = [:]
                var removed: Set<String> = []
                for change in page.changes where change.kind == "message" {
                    if change.action == "delete" {
                        updated[change.id] = nil
                        removed.insert(change.id)
                        continue
                    }
                    guard change.action == "upsert", let folder = change.folderId, let received = change.receivedAt,
                          let version = change.keyVersion, let read = change.isRead else { throw HostedMailboxIssue.corrupt }
                    let entry = EncryptedMailboxCache.Entry(id: change.id, folder: folder, received: received,
                                                           keyVersion: version, read: read == 1)
                    let header: HostedMailHeader
                    if let current = headers[entry.id], index.entries[entry.id]?.keyVersion == version,
                       !removed.contains(entry.id) {
                        header = current
                    } else {
                        let bytes = try await api.ciphertext(entry.id)
                        let plaintext = try HostedEnvelope.open(bytes, profile: api.profile, id: entry.id, keyVersion: version)
                        header = hostedHeaders(raw: plaintext)
                        try cache.store(bytes, id: entry.id)
                    }
                    removed.remove(entry.id)
                    updated[entry.id] = (entry, header)
                }
                // Read the current index after network awaits to retain draft edits.
                var revised = index
                revised.address = account.0
                revised.folders = account.1
                revised.cursor = page.cursor
                for (id, record) in updated { revised.entries[id] = record.0 }
                for id in removed { revised.entries[id] = nil }
                try cache.save(revised)
                index = revised
                for (id, record) in updated { headers[id] = record.1 }
                for id in removed { headers[id] = nil; cache.remove(id) }
                if !foldersContainSelection() { folder = self.folders.first { $0.role == "inbox" }?.id }
                if !page.hasMore { break }
                guard page.cursor > cursor else { throw HostedMailboxIssue.corrupt }
                cursor = page.cursor
            }
            status = nil
        } catch {
            status = message(for: error)
        }
        if draft.submission != nil { await checkSubmission() }
    }

    private func foldersContainSelection() -> Bool {
        folders.contains { $0.id == folder }
    }

    func openSelected() {
        closeMessage()
        guard let entry = currentEntry, let cache, let api else { return }
        do {
            guard let bytes = try cache.read(entry.id) else { throw HostedMailboxIssue.corrupt }
            let plaintext = try HostedEnvelope.open(bytes, profile: api.profile, id: entry.id, keyVersion: entry.keyVersion)
            reading = hostedReading(raw: plaintext)
            raw = plaintext
        } catch { status = message(for: error) }
    }

    private func closeMessage() {
        raw = nil
        reading = nil
    }

    func changeSelected(folder newFolder: String? = nil, read: Bool? = nil) async {
        guard let id = selected, let api else { return }
        do {
            try await api.move(id, folder: newFolder, read: read)
            if newFolder != nil, selected == id { selected = nil }
            await refresh()
        } catch { status = message(for: error) }
    }

    func attachment(_ item: HostedAttachment) -> Data? {
        guard let raw else { return nil }
        return hostedAttachment(raw: raw, partId: item.partId)
    }

    func saveDraft(_ draft: HostedDraft?) {
        var revised = index
        revised.draft = draft
        do {
            guard let cache else { throw HostedMailboxIssue.profile }
            try cache.save(revised)
            index = revised
        } catch { sendStatus = "The draft could not be saved locally." }
    }

    func discardDraft() {
        guard !sending, draft.pending == nil || failedSubmission else { return }
        saveDraft(nil)
        if !hasDraft { sendStatus = nil; failedSubmission = false }
    }

    func prepareReply() {
        guard let header = currentHeader else { return }
        var reply = HostedDraft()
        reply.to = reading?.replyTo.isEmpty == false ? reading?.replyTo ?? header.fromAddress : header.fromAddress
        reply.subject = header.subject.lowercased().hasPrefix("re: ") ? header.subject : "Re: " + header.subject
        reply.replyToID = header.messageId
        reply.references = header.references
        saveDraft(reply)
    }

    func send() async {
        guard let api, let address, hasDraft, !sending, draft.submission == nil else { return }
        let firstAttempt = draft.pending == nil
        sending = true
        defer { sending = false }
        do {
            var draft = draft
            if draft.pending == nil {
                let payload = try HostedMessageWriter.payload(draft, from: address)
                let key = SHA256.hash(data: Data(UUID().uuidString.utf8)).map { String(format: "%02x", $0) }.joined()
                draft.pending = HostedDraft.Pending(key: key, payload: payload)
                saveDraft(draft)
            }
            // A failed disk write must not turn a retry into a second send.
            guard let pending = self.draft.pending else { return }
            let result = try await api.send(pending.payload, key: pending.key)
            draft.submission = result.id
            saveDraft(draft)
            handleSubmission(result)
        } catch HostedMailboxIssue.profile {
            sendStatus = "Enter one valid email address and shorten the subject or message."
        } catch HostedMailboxIssue.relay {
            if firstAttempt {
                var editable = draft
                editable.pending = nil
                saveDraft(editable)
            }
            sendStatus = HostedMailboxIssue.relay.errorDescription
        } catch {
            sendStatus = message(for: error)
        }
        await refresh()
    }

    func checkSubmission() async {
        guard let api, let id = draft.submission else { return }
        do {
            let result = try await api.submission(id)
            if draft.submission == id { handleSubmission(result) }
        } catch {
            if draft.submission == id { sendStatus = message(for: error) }
        }
    }

    private func handleSubmission(_ result: EncryptedMailboxAPI.Submission) {
        failedSubmission = result.state == "failed" || result.state == "bounced"
        switch result.state {
        case "queued": sendStatus = "Queued for delivery."
        case "accepted":
            sendStatus = "Accepted by the delivery relay."
            saveDraft(nil)
        case "failed", "bounced":
            sendStatus = result.failureReason == "delivery_outcome_unknown"
                ? "The delivery outcome is unknown. Check with the recipient before sending again."
                : "Delivery failed. Your draft is still here."
        default: sendStatus = "The delivery status is unavailable."
        }
    }

    private func message(for error: any Error) -> String {
        if error is URLError { return "The hosted mailbox could not be reached." }
        return (error as? HostedMailboxIssue)?.errorDescription ?? "The mailbox operation could not be completed."
    }
}
#endif
