// Written by `just apple gen-api` from the routes' schemas: the OpenAPI document tests/support/openapi_doc.py makes, as the
// page's web/src/lib/api/schema.gen.ts is. Never edit it by hand: `just apple check-api` fails when it no longer matches.
// swiftlint:disable all
import Foundation

/// The routes' answers and bodies, each route under /api/ by method (`API.Get`, `API.Post`).
public enum API {}

extension API {
    /// A copy of a message, one of many a request names: its key, the folder it is in and its uid there. Without a uid it is
    /// the only copy of that key in that folder, or none.
    public struct ACopy: Codable, Hashable, Sendable {
        public var folder: String
        public var key: Int
        public var uid: Int?

        public init(folder: String, key: Int, uid: Int? = nil) {
            self.folder = folder
            self.key = key
            self.uid = uid
        }
    }

    /// An account to add, or what an edit changes (`account.apply_fields`). The page sends none of `strip_spaces` and
    /// `disabled`, which the use case still reads, and `trash` only as a preset names it.
    public struct AccountBody: Codable, Hashable, Sendable {
        public var alias: String?
        public var credential: JSONValue?
        public var disabled: Bool?
        public var email: String?
        public var host: JSONValue?
        public var port: JSONValue?
        public var signature: String?
        public var smtpHost: JSONValue?
        public var smtpPort: JSONValue?
        public var smtpStarttls: Bool?
        public var starttls: Bool?
        public var stripSpaces: Bool?
        public var trash: String?
        public var type: JSONValue?

        public init(alias: String? = nil, credential: JSONValue? = nil, disabled: Bool? = nil, email: String? = nil, host: JSONValue? = nil, port: JSONValue? = nil, signature: String? = nil, smtpHost: JSONValue? = nil, smtpPort: JSONValue? = nil, smtpStarttls: Bool? = nil, starttls: Bool? = nil, stripSpaces: Bool? = nil, trash: String? = nil, type: JSONValue? = nil) {
            self.alias = alias
            self.credential = credential
            self.disabled = disabled
            self.email = email
            self.host = host
            self.port = port
            self.signature = signature
            self.smtpHost = smtpHost
            self.smtpPort = smtpPort
            self.smtpStarttls = smtpStarttls
            self.starttls = starttls
            self.stripSpaces = stripSpaces
            self.trash = trash
            self.type = type
        }

        enum CodingKeys: String, CodingKey {
            case alias
            case credential
            case disabled
            case email
            case host
            case port
            case signature
            case smtpHost = "smtp_host"
            case smtpPort = "smtp_port"
            case smtpStarttls = "smtp_starttls"
            case starttls
            case stripSpaces = "strip_spaces"
            case trash
            case type
        }
    }

    /// An account not saved yet, as `draft.throwaway_account` reads one. What it can't read is a failed test, not a failed
    /// request, and it refuses a stored secret in words of its own, so what it judges stays loose.
    public struct AccountDraft: Codable, Hashable, Sendable {
        public var credential: JSONValue?
        public var email: String?
        public var host: JSONValue?
        public var port: JSONValue?
        public var smtpHost: JSONValue?
        public var smtpPort: JSONValue?
        public var smtpStarttls: Bool?
        public var starttls: Bool?
        public var stripSpaces: Bool?
        public var type: JSONValue?

        public init(credential: JSONValue? = nil, email: String? = nil, host: JSONValue? = nil, port: JSONValue? = nil, smtpHost: JSONValue? = nil, smtpPort: JSONValue? = nil, smtpStarttls: Bool? = nil, starttls: Bool? = nil, stripSpaces: Bool? = nil, type: JSONValue? = nil) {
            self.credential = credential
            self.email = email
            self.host = host
            self.port = port
            self.smtpHost = smtpHost
            self.smtpPort = smtpPort
            self.smtpStarttls = smtpStarttls
            self.starttls = starttls
            self.stripSpaces = stripSpaces
            self.type = type
        }

        enum CodingKeys: String, CodingKey {
            case credential
            case email
            case host
            case port
            case smtpHost = "smtp_host"
            case smtpPort = "smtp_port"
            case smtpStarttls = "smtp_starttls"
            case starttls
            case stripSpaces = "strip_spaces"
            case type
        }

        /// The keys it names, and those it requires, which a union's decoding goes by.
        static let named: Set<String> = ["credential", "email", "host", "port", "smtp_host", "smtp_port", "smtp_starttls", "starttls", "strip_spaces", "type"]
        static let required: Set<String> = []
    }

    public struct AccountReconciled: Codable, Hashable, Sendable {
        public var account: String
        public var error: String?
        public var folders: Int
        public var ghosts: Int
        public var live: Int
        public var pruned: Int
        public var stored: Int

        public init(account: String, error: String? = nil, folders: Int, ghosts: Int, live: Int, pruned: Int, stored: Int) {
            self.account = account
            self.error = error
            self.folders = folders
            self.ghosts = ghosts
            self.live = live
            self.pruned = pruned
            self.stored = stored
        }
    }

    /// `account.masked_row`: the servers the account reaches, incoming and outgoing, which the account form starts from.
    /// An outgoing server none was saved for is its type's, else the incoming host (`defaults_applied` says which).
    public struct AccountRow: Codable, Hashable, Sendable {
        public var alias: String?
        public var credential: CredentialShown
        public var defaultsApplied: DefaultsApplied
        public var disabled: Bool
        public var email: String?
        public var host: String?
        public var port: Int
        public var signature: String
        public var smtpHost: String?
        public var smtpPort: Int
        public var smtpStarttls: Bool
        public var starttls: Bool
        public var trash: String
        public var type: String

        public init(alias: String? = nil, credential: CredentialShown, defaultsApplied: DefaultsApplied, disabled: Bool, email: String? = nil, host: String? = nil, port: Int, signature: String, smtpHost: String? = nil, smtpPort: Int, smtpStarttls: Bool, starttls: Bool, trash: String, type: String) {
            self.alias = alias
            self.credential = credential
            self.defaultsApplied = defaultsApplied
            self.disabled = disabled
            self.email = email
            self.host = host
            self.port = port
            self.signature = signature
            self.smtpHost = smtpHost
            self.smtpPort = smtpPort
            self.smtpStarttls = smtpStarttls
            self.starttls = starttls
            self.trash = trash
            self.type = type
        }

        enum CodingKeys: String, CodingKey {
            case alias
            case credential
            case defaultsApplied = "defaults_applied"
            case disabled
            case email
            case host
            case port
            case signature
            case smtpHost = "smtp_host"
            case smtpPort = "smtp_port"
            case smtpStarttls = "smtp_starttls"
            case starttls
            case trash
            case type
        }
    }

    /// `AccountHealth.as_row`. Of an error, the class's name and its kind, never what it said.
    public struct AccountStanding: Codable, Hashable, Sendable {
        public var account: String
        public var healthy: Bool
        public var lastError: String?
        public var lastErrorKind: String?
        public var lastErrorTs: String?
        public var lastOkTs: String?
        public var live: String
        public var syncCount: Int

        public init(account: String, healthy: Bool, lastError: String? = nil, lastErrorKind: String? = nil, lastErrorTs: String? = nil, lastOkTs: String? = nil, live: String, syncCount: Int) {
            self.account = account
            self.healthy = healthy
            self.lastError = lastError
            self.lastErrorKind = lastErrorKind
            self.lastErrorTs = lastErrorTs
            self.lastOkTs = lastOkTs
            self.live = live
            self.syncCount = syncCount
        }

        enum CodingKeys: String, CodingKey {
            case account
            case healthy
            case lastError = "last_error"
            case lastErrorKind = "last_error_kind"
            case lastErrorTs = "last_error_ts"
            case lastOkTs = "last_ok_ts"
            case live
            case syncCount = "sync_count"
        }
    }

    public struct AccountStats: Codable, Hashable, Sendable {
        public var account: String
        public var alias: String?
        public var byBucket: [String: Int]
        public var error: String?
        public var inInbox: Int
        public var junk: Int
        public var keep: Int
        public var kind: String
        public var sweepable: Int
        public var topSenders: [String: [String]]

        public init(account: String, alias: String? = nil, byBucket: [String: Int], error: String? = nil, inInbox: Int, junk: Int, keep: Int, kind: String, sweepable: Int, topSenders: [String: [String]]) {
            self.account = account
            self.alias = alias
            self.byBucket = byBucket
            self.error = error
            self.inInbox = inInbox
            self.junk = junk
            self.keep = keep
            self.kind = kind
            self.sweepable = sweepable
            self.topSenders = topSenders
        }

        enum CodingKeys: String, CodingKey {
            case account
            case alias
            case byBucket = "by_bucket"
            case error
            case inInbox = "in_inbox"
            case junk
            case keep
            case kind
            case sweepable
            case topSenders = "top_senders"
        }
    }

    /// One account's sweep. One that failed on the way in says only how far it got.
    public struct AccountSwept: Codable, Hashable, Sendable {
        public var account: String
        public var byBucket: [String: Int]?
        public var capped: Bool?
        public var error: String?
        public var going: [SweepBucket]?
        public var heldBack: Int?
        public var moved: Int?
        public var noUndo: Int?
        public var planned: Int
        public var senders: Int?
        public var stragglers: [String]?

        public init(account: String, byBucket: [String: Int]? = nil, capped: Bool? = nil, error: String? = nil, going: [SweepBucket]? = nil, heldBack: Int? = nil, moved: Int? = nil, noUndo: Int? = nil, planned: Int, senders: Int? = nil, stragglers: [String]? = nil) {
            self.account = account
            self.byBucket = byBucket
            self.capped = capped
            self.error = error
            self.going = going
            self.heldBack = heldBack
            self.moved = moved
            self.noUndo = noUndo
            self.planned = planned
            self.senders = senders
            self.stragglers = stragglers
        }

        enum CodingKeys: String, CodingKey {
            case account
            case byBucket = "by_bucket"
            case capped
            case error
            case going
            case heldBack = "held_back"
            case moved
            case noUndo = "no_undo"
            case planned
            case senders
            case stragglers
        }
    }

    public struct AccountSync: Codable, Hashable, Sendable {
        public var done: Int?
        public var health: String
        public var msg: String
        public var total: Int?
        public var tryingAgain: Bool?

        public init(done: Int? = nil, health: String, msg: String, total: Int? = nil, tryingAgain: Bool? = nil) {
            self.done = done
            self.health = health
            self.msg = msg
            self.total = total
            self.tryingAgain = tryingAgain
        }

        enum CodingKeys: String, CodingKey {
            case done
            case health
            case msg
            case total
            case tryingAgain = "trying_again"
        }
    }

    /// One of the people a message went to, as its To or Cc names them (`kernel.addressees`).
    public struct Addressee: Codable, Hashable, Sendable {
        public var addr: String
        public var name: String?

        public init(addr: String, name: String? = nil) {
            self.addr = addr
            self.name = name
        }
    }

    public struct AiCosts: Codable, Hashable, Sendable {
        public var batch: Batch
        public var days: [DayCost]
        public var generated: String
        public var governor: Governor
        public var ok: Bool

        public init(batch: Batch, days: [DayCost], generated: String, governor: Governor, ok: Bool) {
            self.batch = batch
            self.days = days
            self.generated = generated
            self.governor = governor
            self.ok = ok
        }
    }

    public enum AiOff: String, Codable, Hashable, Sendable, CaseIterable {
        case turnedOff = "turned-off"
        case noKey = "no-key"
        case noModel = "no-model"
        case noUrl = "no-url"
    }

    public enum AiProvider: String, Codable, Hashable, Sendable, CaseIterable {
        case anthropic
        case openai
        case gemini
        case local
        case compatible
    }

    /// `Status.report`: how AI stands, with no call made. `provider` is the one chosen, and `off` why AI is off (None while it
    /// is on). `model` is what a person's asks go to, and `background_model` what background summaries ask: "" while AI is off.
    /// `cloud_model` is whether the model chosen for a server on this Mac (a model on this Mac, or a compatible service on
    /// loopback) runs in a cloud, as Ollama's `-cloud` models do, so mail asked of it leaves this Mac: said whether AI is on
    /// or off. `summaries_waiting` is how many Inbox messages from the last 30 days wait for a summary, and `summarizing` whether
    /// the background reads them, now or at its next round, with neither today's limit nor the breaker holding them back: each
    /// there while AI is on. Until those summaries land, what needs you isn't known.
    public struct AiStatus: Codable, Hashable, Sendable {
        public var backgroundModel: String
        public var batchWaiting: Int?
        public var cloudModel: Bool
        public var engine: String
        public var keyFrom: String
        public var lastError: String?
        public var lastOk: Bool?
        public var lastTs: Int
        public var limitMicros: Int
        public var model: String
        public var off: AiOff?
        public var provider: AiProvider
        public var spentTodayMicros: Int?
        public var summariesWaiting: Int?
        public var summarizing: Bool?

        public init(backgroundModel: String, batchWaiting: Int? = nil, cloudModel: Bool, engine: String, keyFrom: String, lastError: String? = nil, lastOk: Bool? = nil, lastTs: Int, limitMicros: Int, model: String, off: AiOff? = nil, provider: AiProvider, spentTodayMicros: Int? = nil, summariesWaiting: Int? = nil, summarizing: Bool? = nil) {
            self.backgroundModel = backgroundModel
            self.batchWaiting = batchWaiting
            self.cloudModel = cloudModel
            self.engine = engine
            self.keyFrom = keyFrom
            self.lastError = lastError
            self.lastOk = lastOk
            self.lastTs = lastTs
            self.limitMicros = limitMicros
            self.model = model
            self.off = off
            self.provider = provider
            self.spentTodayMicros = spentTodayMicros
            self.summariesWaiting = summariesWaiting
            self.summarizing = summarizing
        }

        enum CodingKeys: String, CodingKey {
            case backgroundModel = "background_model"
            case batchWaiting = "batch_waiting"
            case cloudModel = "cloud_model"
            case engine
            case keyFrom = "key_from"
            case lastError = "last_error"
            case lastOk = "last_ok"
            case lastTs = "last_ts"
            case limitMicros = "limit_micros"
            case model
            case off
            case provider
            case spentTodayMicros = "spent_today_micros"
            case summariesWaiting = "summaries_waiting"
            case summarizing
        }
    }

    public struct AiToday: Codable, Hashable, Sendable {
        public var batch: Batch
        public var limitMicros: Int
        public var today: DayCost

        public init(batch: Batch, limitMicros: Int, today: DayCost) {
            self.batch = batch
            self.limitMicros = limitMicros
            self.today = today
        }

        enum CodingKeys: String, CodingKey {
            case batch
            case limitMicros = "limit_micros"
            case today
        }
    }

    public struct Answered: Codable, Hashable, Sendable {
        public var account: String
        public var answer: String
        public var generated: String
        public var model: String
        public var ok: Bool
        public var question: String
        public var sources: [AskSource]

        public init(account: String, answer: String, generated: String, model: String, ok: Bool, question: String, sources: [AskSource]) {
            self.account = account
            self.answer = answer
            self.generated = generated
            self.model = model
            self.ok = ok
            self.question = question
            self.sources = sources
        }
    }

    public enum ArchiveFormat: String, Codable, Hashable, Sendable, CaseIterable {
        case eml
        case mbox
    }

    public struct AskBody: Codable, Hashable, Sendable {
        public var account: String?
        public var question: String?

        public init(account: String? = nil, question: String? = nil) {
            self.account = account
            self.question = question
        }
    }

    public struct AskSource: Codable, Hashable, Sendable {
        public var account: String
        public var date: String
        public var folder: String
        public var `from`: String?
        public var key: Int
        public var snippet: String
        public var subject: String
        public var uid: Int

        public init(account: String, date: String, folder: String, `from`: String? = nil, key: Int, snippet: String, subject: String, uid: Int) {
            self.account = account
            self.date = date
            self.folder = folder
            self.`from` = `from`
            self.key = key
            self.snippet = snippet
            self.subject = subject
            self.uid = uid
        }
    }

    public struct AtAGlance: Codable, Hashable, Sendable {
        public var ai: AiToday
        public var breakers: [String]
        public var generated: String
        public var idle: Idle
        public var jobs: [Job]
        public var ok: Bool
        public var profile: String
        public var queues: Queues

        public init(ai: AiToday, breakers: [String], generated: String, idle: Idle, jobs: [Job], ok: Bool, profile: String, queues: Queues) {
            self.ai = ai
            self.breakers = breakers
            self.generated = generated
            self.idle = idle
            self.jobs = jobs
            self.ok = ok
            self.profile = profile
            self.queues = queues
        }
    }

    public struct AttachmentPart: Codable, Hashable, Sendable {
        public var contentId: String?
        public var contentType: String
        public var filename: String
        public var isInline: Int
        public var partId: String
        public var size: Int
        public var travels: Bool

        public init(contentId: String? = nil, contentType: String, filename: String, isInline: Int, partId: String, size: Int, travels: Bool) {
            self.contentId = contentId
            self.contentType = contentType
            self.filename = filename
            self.isInline = isInline
            self.partId = partId
            self.size = size
            self.travels = travels
        }

        enum CodingKeys: String, CodingKey {
            case contentId = "content_id"
            case contentType = "content_type"
            case filename
            case isInline = "is_inline"
            case partId = "part_id"
            case size
            case travels
        }
    }

    /// A record of an audit trail: a sweep's message, a sender's cleanup or a reconcile's count.
    public struct AuditEvent: Codable, Hashable, Sendable {
        public var account: String?
        public var auto: Bool?
        public var bucket: String?
        public var `from`: String?
        public var kind: String
        public var live: Int?
        public var pruned: Int?
        public var reason: String?
        public var status: String?
        public var subject: String?
        public var swept: Int?
        public var token: String?
        public var ts: String
        public var uid: String?

        public init(account: String? = nil, auto: Bool? = nil, bucket: String? = nil, `from`: String? = nil, kind: String, live: Int? = nil, pruned: Int? = nil, reason: String? = nil, status: String? = nil, subject: String? = nil, swept: Int? = nil, token: String? = nil, ts: String, uid: String? = nil) {
            self.account = account
            self.auto = auto
            self.bucket = bucket
            self.`from` = `from`
            self.kind = kind
            self.live = live
            self.pruned = pruned
            self.reason = reason
            self.status = status
            self.subject = subject
            self.swept = swept
            self.token = token
            self.ts = ts
            self.uid = uid
        }
    }

    public struct AuditTrail: Codable, Hashable, Sendable {
        public var events: [AuditEvent]
        public var hasMore: Bool

        public init(events: [AuditEvent], hasMore: Bool) {
            self.events = events
            self.hasMore = hasMore
        }

        enum CodingKeys: String, CodingKey {
            case events
            case hasMore = "has_more"
        }
    }

    /// `Assessments.batch_status`.
    public struct Batch: Codable, Hashable, Sendable {
        public var estimateMicros: Int
        public var state: String?
        public var waiting: Int

        public init(estimateMicros: Int, state: String? = nil, waiting: Int) {
            self.estimateMicros = estimateMicros
            self.state = state
            self.waiting = waiting
        }

        enum CodingKeys: String, CodingKey {
            case estimateMicros = "estimate_micros"
            case state
            case waiting
        }
    }

    public struct BatchCleanedUp: Codable, Hashable, Sendable {
        public var blocked: [String]
        public var ok: Bool
        public var reversible: Reversible
        public var sweepCapped: Bool
        public var sweepFailed: Int
        public var sweepQueued: Int
        public var swept: Int
        public var undoGroup: String

        public init(blocked: [String], ok: Bool, reversible: Reversible, sweepCapped: Bool, sweepFailed: Int, sweepQueued: Int, swept: Int, undoGroup: String) {
            self.blocked = blocked
            self.ok = ok
            self.reversible = reversible
            self.sweepCapped = sweepCapped
            self.sweepFailed = sweepFailed
            self.sweepQueued = sweepQueued
            self.swept = swept
            self.undoGroup = undoGroup
        }

        enum CodingKeys: String, CodingKey {
            case blocked
            case ok
            case reversible
            case sweepCapped = "sweep_capped"
            case sweepFailed = "sweep_failed"
            case sweepQueued = "sweep_queued"
            case swept
            case undoGroup = "undo_group"
        }
    }

    public struct BatchCleanupBody: Codable, Hashable, Sendable {
        public var senders: [String]?
        public var undoGroup: String?

        public init(senders: [String]? = nil, undoGroup: String? = nil) {
            self.senders = senders
            self.undoGroup = undoGroup
        }

        enum CodingKeys: String, CodingKey {
            case senders
            case undoGroup = "undo_group"
        }
    }

    /// What cleaning up these senders together would do. Only the block and the sweep: each unsubscribe wants its own preview.
    public struct BatchPreview: Codable, Hashable, Sendable {
        public var ok: Bool
        public var reversible: Reversible
        public var senders: [BatchSender]
        public var sweepCapped: Bool
        public var sweepCount: Int

        public init(ok: Bool, reversible: Reversible, senders: [BatchSender], sweepCapped: Bool, sweepCount: Int) {
            self.ok = ok
            self.reversible = reversible
            self.senders = senders
            self.sweepCapped = sweepCapped
            self.sweepCount = sweepCount
        }

        enum CodingKeys: String, CodingKey {
            case ok
            case reversible
            case senders
            case sweepCapped = "sweep_capped"
            case sweepCount = "sweep_count"
        }
    }

    public struct BatchSender: Codable, Hashable, Sendable {
        public var count: Int
        public var fromAddr: String
        public var sweepCount: Int
        public var unseen: Int

        public init(count: Int, fromAddr: String, sweepCount: Int, unseen: Int) {
            self.count = count
            self.fromAddr = fromAddr
            self.sweepCount = sweepCount
            self.unseen = unseen
        }

        enum CodingKeys: String, CodingKey {
            case count
            case fromAddr = "from_addr"
            case sweepCount = "sweep_count"
            case unseen
        }
    }

    public struct BatchSendersBody: Codable, Hashable, Sendable {
        public var senders: [String]?

        public init(senders: [String]? = nil) {
            self.senders = senders
        }
    }

    public enum BlockScope: String, Codable, Hashable, Sendable, CaseIterable {
        case sender
        case domain
    }

    public struct Brief: Codable, Hashable, Sendable {
        public var cached: Bool
        public var generated: String
        public var groups: [BriefGroup]
        public var model: String
        public var narrative: String
        public var ok: Bool
        public var stale: Bool

        public init(cached: Bool, generated: String, groups: [BriefGroup], model: String, narrative: String, ok: Bool, stale: Bool) {
            self.cached = cached
            self.generated = generated
            self.groups = groups
            self.model = model
            self.narrative = narrative
            self.ok = ok
            self.stale = stale
        }

        /// The keys it names, and those it requires, which a union's decoding goes by.
        static let named: Set<String> = ["cached", "generated", "groups", "model", "narrative", "ok", "stale"]
        static let required: Set<String> = ["cached", "generated", "groups", "model", "narrative", "ok", "stale"]
    }

    public struct BriefGroup: Codable, Hashable, Sendable {
        public var count: Int
        public var label: String
        public var messages: [Copy]

        public init(count: Int, label: String, messages: [Copy]) {
            self.count = count
            self.label = label
            self.messages = messages
        }
    }

    /// None is stored yet, and a read writes none.
    public struct BriefMissing: Codable, Hashable, Sendable {
        public var cached: Bool
        public var groups: [BriefGroup]
        public var missing: Bool
        public var narrative: String
        public var ok: Bool
        public var stale: Bool

        public init(cached: Bool, groups: [BriefGroup], missing: Bool, narrative: String, ok: Bool, stale: Bool) {
            self.cached = cached
            self.groups = groups
            self.missing = missing
            self.narrative = narrative
            self.ok = ok
            self.stale = stale
        }

        /// The keys it names, and those it requires, which a union's decoding goes by.
        static let named: Set<String> = ["cached", "groups", "missing", "narrative", "ok", "stale"]
        static let required: Set<String> = ["cached", "groups", "missing", "narrative", "ok", "stale"]
    }

    public struct Bucket: Codable, Hashable, Sendable {
        public var isJunk: Bool
        public var name: String

        public init(isJunk: Bool, name: String) {
            self.isJunk = isJunk
            self.name = name
        }

        enum CodingKeys: String, CodingKey {
            case isJunk = "is_junk"
            case name
        }
    }

    public struct BulkFileBody: Codable, Hashable, Sendable {
        public var mutationId: String?
        public var rows: [BulkRow]?
        public var undoGroup: String?
        public var verb: FilingVerb?

        public init(mutationId: String? = nil, rows: [BulkRow]? = nil, undoGroup: String? = nil, verb: FilingVerb? = nil) {
            self.mutationId = mutationId
            self.rows = rows
            self.undoGroup = undoGroup
            self.verb = verb
        }

        enum CodingKeys: String, CodingKey {
            case mutationId = "mutation_id"
            case rows
            case undoGroup = "undo_group"
            case verb
        }
    }

    public struct BulkMoveBody: Codable, Hashable, Sendable {
        public var mutationId: String?
        public var rows: [BulkRow]?
        public var to: String
        public var undoGroup: String?

        public init(mutationId: String? = nil, rows: [BulkRow]? = nil, to: String, undoGroup: String? = nil) {
            self.mutationId = mutationId
            self.rows = rows
            self.to = to
            self.undoGroup = undoGroup
        }

        enum CodingKeys: String, CodingKey {
            case mutationId = "mutation_id"
            case rows
            case to
            case undoGroup = "undo_group"
        }
    }

    public struct BulkRow: Codable, Hashable, Sendable {
        public var folder: String
        public var key: Int
        public var uid: Int?
        public var whole: Bool?

        public init(folder: String, key: Int, uid: Int? = nil, whole: Bool? = nil) {
            self.folder = folder
            self.key = key
            self.uid = uid
            self.whole = whole
        }
    }

    public struct Cancelled: Codable, Hashable, Sendable {
        public var ok: Bool

        public init(ok: Bool) {
            self.ok = ok
        }
    }

    /// What changes. What a request doesn't name keeps what it was, so two tabs can each change their own.
    public struct ChangedPreferences: Codable, Hashable, Sendable {
        public var aiBaseUrl: String?
        public var aiDailyLimitUsd: Double?
        public var aiEnabled: Bool?
        public var aiModel: String?
        public var aiProvider: AiProvider?
        public var autoTidy: Bool?
        public var conversations: Bool?
        public var density: Density?
        public var draftAhead: Bool?
        public var draftTone: DraftTone?
        public var guideOffered: Bool?
        public var highContrast: Bool?
        public var mailColors: MailColors?
        public var morningBrief: Bool?
        public var notifyMeetings: Bool?
        public var notifyNewMail: Bool?
        public var quietFrom: String?
        public var quietHours: Bool?
        public var quietUntil: String?
        public var readFont: ReadFont?
        public var readSize: ReadSize?
        public var readWidth: ReadWidth?
        public var searchScope: SearchScope?
        public var singleKeys: Bool?
        public var theme: Theme?
        public var undoSendSeconds: Int?
        public var weeklyReview: Bool?

        public init(aiBaseUrl: String? = nil, aiDailyLimitUsd: Double? = nil, aiEnabled: Bool? = nil, aiModel: String? = nil, aiProvider: AiProvider? = nil, autoTidy: Bool? = nil, conversations: Bool? = nil, density: Density? = nil, draftAhead: Bool? = nil, draftTone: DraftTone? = nil, guideOffered: Bool? = nil, highContrast: Bool? = nil, mailColors: MailColors? = nil, morningBrief: Bool? = nil, notifyMeetings: Bool? = nil, notifyNewMail: Bool? = nil, quietFrom: String? = nil, quietHours: Bool? = nil, quietUntil: String? = nil, readFont: ReadFont? = nil, readSize: ReadSize? = nil, readWidth: ReadWidth? = nil, searchScope: SearchScope? = nil, singleKeys: Bool? = nil, theme: Theme? = nil, undoSendSeconds: Int? = nil, weeklyReview: Bool? = nil) {
            self.aiBaseUrl = aiBaseUrl
            self.aiDailyLimitUsd = aiDailyLimitUsd
            self.aiEnabled = aiEnabled
            self.aiModel = aiModel
            self.aiProvider = aiProvider
            self.autoTidy = autoTidy
            self.conversations = conversations
            self.density = density
            self.draftAhead = draftAhead
            self.draftTone = draftTone
            self.guideOffered = guideOffered
            self.highContrast = highContrast
            self.mailColors = mailColors
            self.morningBrief = morningBrief
            self.notifyMeetings = notifyMeetings
            self.notifyNewMail = notifyNewMail
            self.quietFrom = quietFrom
            self.quietHours = quietHours
            self.quietUntil = quietUntil
            self.readFont = readFont
            self.readSize = readSize
            self.readWidth = readWidth
            self.searchScope = searchScope
            self.singleKeys = singleKeys
            self.theme = theme
            self.undoSendSeconds = undoSendSeconds
            self.weeklyReview = weeklyReview
        }

        enum CodingKeys: String, CodingKey {
            case aiBaseUrl = "ai_base_url"
            case aiDailyLimitUsd = "ai_daily_limit_usd"
            case aiEnabled = "ai_enabled"
            case aiModel = "ai_model"
            case aiProvider = "ai_provider"
            case autoTidy = "auto_tidy"
            case conversations
            case density
            case draftAhead = "draft_ahead"
            case draftTone = "draft_tone"
            case guideOffered = "guide_offered"
            case highContrast = "high_contrast"
            case mailColors = "mail_colors"
            case morningBrief = "morning_brief"
            case notifyMeetings = "notify_meetings"
            case notifyNewMail = "notify_new_mail"
            case quietFrom = "quiet_from"
            case quietHours = "quiet_hours"
            case quietUntil = "quiet_until"
            case readFont = "read_font"
            case readSize = "read_size"
            case readWidth = "read_width"
            case searchScope = "search_scope"
            case singleKeys = "single_keys"
            case theme
            case undoSendSeconds = "undo_send_seconds"
            case weeklyReview = "weekly_review"
        }
    }

    public struct CleanedUp: Codable, Hashable, Sendable {
        public var blockScope: BlockScope
        public var blockedToken: String
        public var browserUrl: String?
        public var httpStatus: Int?
        public var method: String
        public var ok: Bool
        public var reversible: Reversible
        public var status: String
        public var sweepCapped: Bool
        public var sweepFailed: Int
        public var sweepQueued: Int
        public var swept: Int
        public var undoGroup: String

        public init(blockScope: BlockScope, blockedToken: String, browserUrl: String? = nil, httpStatus: Int? = nil, method: String, ok: Bool, reversible: Reversible, status: String, sweepCapped: Bool, sweepFailed: Int, sweepQueued: Int, swept: Int, undoGroup: String) {
            self.blockScope = blockScope
            self.blockedToken = blockedToken
            self.browserUrl = browserUrl
            self.httpStatus = httpStatus
            self.method = method
            self.ok = ok
            self.reversible = reversible
            self.status = status
            self.sweepCapped = sweepCapped
            self.sweepFailed = sweepFailed
            self.sweepQueued = sweepQueued
            self.swept = swept
            self.undoGroup = undoGroup
        }

        enum CodingKeys: String, CodingKey {
            case blockScope = "block_scope"
            case blockedToken = "blocked_token"
            case browserUrl = "browser_url"
            case httpStatus = "http_status"
            case method
            case ok
            case reversible
            case status
            case sweepCapped = "sweep_capped"
            case sweepFailed = "sweep_failed"
            case sweepQueued = "sweep_queued"
            case swept
            case undoGroup = "undo_group"
        }
    }

    public struct CleanupBody: Codable, Hashable, Sendable {
        public var blockScope: BlockScope?
        public var confirmToken: String?
        public var doUnsubscribe: Bool?
        public var fromAddr: String?
        public var undoGroup: String?

        public init(blockScope: BlockScope? = nil, confirmToken: String? = nil, doUnsubscribe: Bool? = nil, fromAddr: String? = nil, undoGroup: String? = nil) {
            self.blockScope = blockScope
            self.confirmToken = confirmToken
            self.doUnsubscribe = doUnsubscribe
            self.fromAddr = fromAddr
            self.undoGroup = undoGroup
        }

        enum CodingKeys: String, CodingKey {
            case blockScope = "block_scope"
            case confirmToken = "confirm_token"
            case doUnsubscribe = "do_unsubscribe"
            case fromAddr = "from_addr"
            case undoGroup = "undo_group"
        }
    }

    public struct CleanupRationales: Codable, Hashable, Sendable {
        public var ok: Bool
        public var rationales: [String: String]

        public init(ok: Bool, rationales: [String: String]) {
            self.ok = ok
            self.rationales = rationales
        }
    }

    public enum ComposeMode: String, Codable, Hashable, Sendable, CaseIterable {
        case new
        case reply
        case replyall
        case forward
    }

    public struct ConfirmBody: Codable, Hashable, Sendable {
        public var plan: String?
        public var undoGroup: String?

        public init(plan: String? = nil, undoGroup: String? = nil) {
            self.plan = plan
            self.undoGroup = undoGroup
        }

        enum CodingKeys: String, CodingKey {
            case plan
            case undoGroup = "undo_group"
        }
    }

    public struct ContactCard: Codable, Hashable, Sendable {
        public var accounts: [String]
        public var addr: String
        public var count: Int
        public var firstAgo: String?
        public var known: Bool
        public var lastAgo: String?
        public var name: String
        public var ok: Bool
        public var recent: [RecentMail]
        public var replied: Int

        public init(accounts: [String], addr: String, count: Int, firstAgo: String? = nil, known: Bool, lastAgo: String? = nil, name: String, ok: Bool, recent: [RecentMail], replied: Int) {
            self.accounts = accounts
            self.addr = addr
            self.count = count
            self.firstAgo = firstAgo
            self.known = known
            self.lastAgo = lastAgo
            self.name = name
            self.ok = ok
            self.recent = recent
            self.replied = replied
        }

        enum CodingKeys: String, CodingKey {
            case accounts
            case addr
            case count
            case firstAgo = "first_ago"
            case known
            case lastAgo = "last_ago"
            case name
            case ok
            case recent
            case replied
        }
    }

    /// What names a copy of a message on the wire: the message's key, the folder the copy is in and its uid there.
    public struct Copy: Codable, Hashable, Sendable {
        public var folder: String
        public var key: Int
        public var uid: Int

        public init(folder: String, key: Int, uid: Int) {
            self.folder = folder
            self.key = key
            self.uid = uid
        }

        /// The keys it names, and those it requires, which a union's decoding goes by.
        static let named: Set<String> = ["folder", "key", "uid"]
        static let required: Set<String> = ["folder", "key", "uid"]
    }

    /// The test ran, and the login didn't work. `error` is its class in `conn_error.quiet`'s words, never what the server
    /// said, the outgoing server's after "SMTP ": the owner's, in small type. `says` is what a person is told of it and what to
    /// do next, and `link` the page that helps, if one does (`login_words`).
    public struct CouldntLogIn: Codable, Hashable, Sendable {
        public var connect: Bool
        public var email: String?
        public var error: String
        public var link: Link?
        public var ok: Bool
        public var says: String

        public init(connect: Bool, email: String? = nil, error: String, link: Link? = nil, ok: Bool, says: String) {
            self.connect = connect
            self.email = email
            self.error = error
            self.link = link
            self.ok = ok
            self.says = says
        }

        /// The keys it names, and those it requires, which a union's decoding goes by.
        static let named: Set<String> = ["connect", "email", "error", "link", "ok", "says"]
        static let required: Set<String> = ["connect", "email", "error", "link", "ok", "says"]
    }

    /// `CredentialRef.masked`: its style, whether it's set, and at most a pointer to it, a Keychain item's name, an op reference
    /// or a variable's name.
    public struct CredentialShown: Codable, Hashable, Sendable {
        public var item: String?
        public var ref: CredentialShownRef?
        public var set: Bool
        public var style: String?
        public var `var`: String?

        public init(item: String? = nil, ref: CredentialShownRef? = nil, set: Bool, style: String? = nil, `var`: String? = nil) {
            self.item = item
            self.ref = ref
            self.set = set
            self.style = style
            self.`var` = `var`
        }
    }

    /// `Gateway.usage`: a local day's calls and what they cost, in all and by feature.
    public struct DayCost: Codable, Hashable, Sendable {
        public var calls: Int
        public var costMicros: Int
        public var day: String
        public var features: [String: FeatureCost]

        public init(calls: Int, costMicros: Int, day: String, features: [String: FeatureCost]) {
            self.calls = calls
            self.costMicros = costMicros
            self.day = day
            self.features = features
        }

        enum CodingKeys: String, CodingKey {
            case calls
            case costMicros = "cost_micros"
            case day
            case features
        }
    }

    /// Which of an account's settings came from its type's defaults. The page doesn't read this.
    public struct DefaultsApplied: Codable, Hashable, Sendable {
        public var host: Bool
        public var port: Bool
        public var smtpHost: Bool
        public var smtpPort: Bool
        public var smtpStarttls: Bool
        public var starttls: Bool
        public var trash: Bool

        public init(host: Bool, port: Bool, smtpHost: Bool, smtpPort: Bool, smtpStarttls: Bool, starttls: Bool, trash: Bool) {
            self.host = host
            self.port = port
            self.smtpHost = smtpHost
            self.smtpPort = smtpPort
            self.smtpStarttls = smtpStarttls
            self.starttls = starttls
            self.trash = trash
        }

        enum CodingKeys: String, CodingKey {
            case host
            case port
            case smtpHost = "smtp_host"
            case smtpPort = "smtp_port"
            case smtpStarttls = "smtp_starttls"
            case starttls
            case trash
        }
    }

    public struct DeleteFolderBody: Codable, Hashable, Sendable {
        public var account: String?
        public var confirm: Bool?
        public var folder: String?

        public init(account: String? = nil, confirm: Bool? = nil, folder: String? = nil) {
            self.account = account
            self.confirm = confirm
            self.folder = folder
        }
    }

    public struct Delta: Codable, Hashable, Sendable {
        public var cached: Bool
        public var count: Int
        public var empty: Bool
        public var headline: String
        public var items: [DeltaItem]
        public var ok: Bool

        public init(cached: Bool, count: Int, empty: Bool, headline: String, items: [DeltaItem], ok: Bool) {
            self.cached = cached
            self.count = count
            self.empty = empty
            self.headline = headline
            self.items = items
            self.ok = ok
        }

        /// The keys it names, and those it requires, which a union's decoding goes by.
        static let named: Set<String> = ["cached", "count", "empty", "headline", "items", "ok"]
        static let required: Set<String> = ["cached", "count", "empty", "headline", "items", "ok"]
    }

    public struct DeltaBody: Codable, Hashable, Sendable {
        public var account: String?
        public var since: DeltaBodySince?

        public init(account: String? = nil, since: DeltaBodySince? = nil) {
            self.account = account
            self.since = since
        }
    }

    /// Nothing new since the time asked about.
    public struct DeltaEmpty: Codable, Hashable, Sendable {
        public var count: Int
        public var empty: Bool
        public var ok: Bool

        public init(count: Int, empty: Bool, ok: Bool) {
            self.count = count
            self.empty = empty
            self.ok = ok
        }

        /// The keys it names, and those it requires, which a union's decoding goes by.
        static let named: Set<String> = ["count", "empty", "ok"]
        static let required: Set<String> = ["count", "empty", "ok"]
    }

    public struct DeltaItem: Codable, Hashable, Sendable {
        public var gist: String
        public var importance: Int
        public var who: String

        public init(gist: String, importance: Int, who: String) {
            self.gist = gist
            self.importance = importance
            self.who = who
        }
    }

    /// There is news, none is stored of it yet, and a read writes none.
    public struct DeltaMissing: Codable, Hashable, Sendable {
        public var count: Int
        public var empty: Bool
        public var missing: Bool
        public var ok: Bool

        public init(count: Int, empty: Bool, missing: Bool, ok: Bool) {
            self.count = count
            self.empty = empty
            self.missing = missing
            self.ok = ok
        }

        /// The keys it names, and those it requires, which a union's decoding goes by.
        static let named: Set<String> = ["count", "empty", "missing", "ok"]
        static let required: Set<String> = ["count", "empty", "missing", "ok"]
    }

    public enum Density: String, Codable, Hashable, Sendable, CaseIterable {
        case comfortable
        case cozy
        case compact
    }

    public struct Digest: Codable, Hashable, Sendable {
        public var accounts: [String]
        public var ai: AiStatus
        public var aliases: [String: String?]
        public var generated: String
        public var items: [DigestItem]
        public var profile: String
        public var sendAccounts: [String]
        public var signatureDefault: String
        public var signatures: [String: String]
        public var stats: DigestStats
        public var status: [String: AccountSync]
        public var sync: SyncState

        public init(accounts: [String], ai: AiStatus, aliases: [String: String?], generated: String, items: [DigestItem], profile: String, sendAccounts: [String], signatureDefault: String, signatures: [String: String], stats: DigestStats, status: [String: AccountSync], sync: SyncState) {
            self.accounts = accounts
            self.ai = ai
            self.aliases = aliases
            self.generated = generated
            self.items = items
            self.profile = profile
            self.sendAccounts = sendAccounts
            self.signatureDefault = signatureDefault
            self.signatures = signatures
            self.stats = stats
            self.status = status
            self.sync = sync
        }

        enum CodingKeys: String, CodingKey {
            case accounts
            case ai
            case aliases
            case generated
            case items
            case profile
            case sendAccounts = "send_accounts"
            case signatureDefault = "signature_default"
            case signatures
            case stats
            case status
            case sync
        }
    }

    public struct DigestItem: Codable, Hashable, Sendable {
        public var account: String
        public var action: String
        public var alias: String?
        public var bucket: String?
        public var category: String?
        public var commitmentWorthy: Bool
        public var flagged: Bool
        public var folder: String
        public var fromAddr: String?
        public var fromName: String?
        public var hasAttach: Bool
        public var importance: Int?
        public var key: Int
        public var kind: String
        public var link: String
        public var needsYou: Bool
        public var replyWorthy: Bool
        public var safeToFile: Bool
        public var seen: Bool
        public var sentTo: [Addressee]
        public var sentToMore: Int
        public var subject: String
        public var summary: String?
        public var threadId: String?
        public var ts: Int?
        public var uid: Int
        public var when: String

        public init(account: String, action: String, alias: String? = nil, bucket: String? = nil, category: String? = nil, commitmentWorthy: Bool, flagged: Bool, folder: String, fromAddr: String? = nil, fromName: String? = nil, hasAttach: Bool, importance: Int? = nil, key: Int, kind: String, link: String, needsYou: Bool, replyWorthy: Bool, safeToFile: Bool, seen: Bool, sentTo: [Addressee], sentToMore: Int, subject: String, summary: String? = nil, threadId: String? = nil, ts: Int? = nil, uid: Int, when: String) {
            self.account = account
            self.action = action
            self.alias = alias
            self.bucket = bucket
            self.category = category
            self.commitmentWorthy = commitmentWorthy
            self.flagged = flagged
            self.folder = folder
            self.fromAddr = fromAddr
            self.fromName = fromName
            self.hasAttach = hasAttach
            self.importance = importance
            self.key = key
            self.kind = kind
            self.link = link
            self.needsYou = needsYou
            self.replyWorthy = replyWorthy
            self.safeToFile = safeToFile
            self.seen = seen
            self.sentTo = sentTo
            self.sentToMore = sentToMore
            self.subject = subject
            self.summary = summary
            self.threadId = threadId
            self.ts = ts
            self.uid = uid
            self.when = when
        }

        enum CodingKeys: String, CodingKey {
            case account
            case action
            case alias
            case bucket
            case category
            case commitmentWorthy = "commitment_worthy"
            case flagged
            case folder
            case fromAddr = "from_addr"
            case fromName = "from_name"
            case hasAttach = "has_attach"
            case importance
            case key
            case kind
            case link
            case needsYou = "needs_you"
            case replyWorthy = "reply_worthy"
            case safeToFile = "safe_to_file"
            case seen
            case sentTo = "sent_to"
            case sentToMore = "sent_to_more"
            case subject
            case summary
            case threadId = "thread_id"
            case ts
            case uid
            case when
        }
    }

    public struct DigestStats: Codable, Hashable, Sendable {
        public var kept: Int
        public var needs: Int
        public var summarized: Int
        public var total: Int
        public var verified: Int

        public init(kept: Int, needs: Int, summarized: Int, total: Int, verified: Int) {
            self.kept = kept
            self.needs = needs
            self.summarized = summarized
            self.total = total
            self.verified = verified
        }
    }

    public struct Document: Codable, Hashable, Sendable {
        public var account: String
        public var filename: String
        public var folder: String
        public var `from`: String
        public var group: String
        public var key: Int
        public var kind: DocumentKind
        public var partId: String
        public var preview: String?
        public var size: Int
        public var subject: String
        public var uid: Int
        public var url: String
        public var when: String

        public init(account: String, filename: String, folder: String, `from`: String, group: String, key: Int, kind: DocumentKind, partId: String, preview: String? = nil, size: Int, subject: String, uid: Int, url: String, when: String) {
            self.account = account
            self.filename = filename
            self.folder = folder
            self.`from` = `from`
            self.group = group
            self.key = key
            self.kind = kind
            self.partId = partId
            self.preview = preview
            self.size = size
            self.subject = subject
            self.uid = uid
            self.url = url
            self.when = when
        }

        enum CodingKeys: String, CodingKey {
            case account
            case filename
            case folder
            case `from`
            case group
            case key
            case kind
            case partId = "part_id"
            case preview
            case size
            case subject
            case uid
            case url
            case when
        }
    }

    public enum DocumentGrouping: String, Codable, Hashable, Sendable, CaseIterable {
        case sender
        case type
    }

    public enum DocumentKind: String, Codable, Hashable, Sendable, CaseIterable {
        case pdf
        case image
        case doc
        case sheet
        case slide
        case archive
        case text
        case other
    }

    public struct DocumentSection: Codable, Hashable, Sendable {
        public var count: Int
        public var id: String
        public var name: String

        public init(count: Int, id: String, name: String) {
            self.count = count
            self.id = id
            self.name = name
        }
    }

    public struct Documents: Codable, Hashable, Sendable {
        public var docs: [Document]
        public var facets: [String: Int]
        public var groups: [DocumentSection]
        public var matched: Int
        public var ok: Bool
        public var shown: Int
        public var total: Int

        public init(docs: [Document], facets: [String: Int], groups: [DocumentSection], matched: Int, ok: Bool, shown: Int, total: Int) {
            self.docs = docs
            self.facets = facets
            self.groups = groups
            self.matched = matched
            self.ok = ok
            self.shown = shown
            self.total = total
        }
    }

    public struct Done: Codable, Hashable, Sendable {
        public var ok: Bool

        public init(ok: Bool) {
            self.ok = ok
        }
    }

    public struct DraftAheadReady: Codable, Hashable, Sendable {
        public var body: String
        public var ok: Bool
        public var status: String
        public var tone: String?

        public init(body: String, ok: Bool, status: String, tone: String? = nil) {
            self.body = body
            self.ok = ok
            self.status = status
            self.tone = tone
        }

        /// The keys it names, and those it requires, which a union's decoding goes by.
        static let named: Set<String> = ["body", "ok", "status", "tone"]
        static let required: Set<String> = ["body", "ok", "status", "tone"]
    }

    public struct DraftAheadStatus: Codable, Hashable, Sendable {
        public var ok: Bool
        public var status: String

        public init(ok: Bool, status: String) {
            self.ok = ok
            self.status = status
        }

        /// The keys it names, and those it requires, which a union's decoding goes by.
        static let named: Set<String> = ["ok", "status"]
        static let required: Set<String> = ["ok", "status"]
    }

    public struct DraftBody: Codable, Hashable, Sendable {
        public var account: String?
        public var attachments: [JSONValue]?
        public var autosave: Bool?
        public var baseVersion: Int?
        public var bcc: DraftBodyBcc?
        public var cc: DraftBodyCc?
        public var delete: Bool?
        public var draftId: Int?
        public var forwardAttachments: Bool?
        public var idempotencyKey: String?
        public var mode: ComposeMode?
        public var originalKey: Int?
        public var resume: [String: JSONValue]?
        public var subject: String?
        public var text: String?
        public var to: DraftBodyTo?

        public init(account: String? = nil, attachments: [JSONValue]? = nil, autosave: Bool? = nil, baseVersion: Int? = nil, bcc: DraftBodyBcc? = nil, cc: DraftBodyCc? = nil, delete: Bool? = nil, draftId: Int? = nil, forwardAttachments: Bool? = nil, idempotencyKey: String? = nil, mode: ComposeMode? = nil, originalKey: Int? = nil, resume: [String: JSONValue]? = nil, subject: String? = nil, text: String? = nil, to: DraftBodyTo? = nil) {
            self.account = account
            self.attachments = attachments
            self.autosave = autosave
            self.baseVersion = baseVersion
            self.bcc = bcc
            self.cc = cc
            self.delete = delete
            self.draftId = draftId
            self.forwardAttachments = forwardAttachments
            self.idempotencyKey = idempotencyKey
            self.mode = mode
            self.originalKey = originalKey
            self.resume = resume
            self.subject = subject
            self.text = text
            self.to = to
        }

        enum CodingKeys: String, CodingKey {
            case account
            case attachments
            case autosave
            case baseVersion = "base_version"
            case bcc
            case cc
            case delete
            case draftId = "draft_id"
            case forwardAttachments = "forward_attachments"
            case idempotencyKey = "idempotency_key"
            case mode
            case originalKey = "original_key"
            case resume
            case subject
            case text
            case to
        }
    }

    /// A save that never saw the latest (`draft.CHANGED_ELSEWHERE`), or any other 409, which says what `Failure` says.
    public struct DraftChanged: Codable, Hashable, Sendable {
        public var aiOff: Bool?
        public var busy: Bool?
        public var conflict: Bool?
        public var detail: String?
        public var limitMicros: Int?
        public var overLimit: Bool?
        public var readOnly: Bool?
        public var spentMicros: Int?
        public var status: Int
        public var title: String
        public var type: String
        public var version: Int?

        public init(aiOff: Bool? = nil, busy: Bool? = nil, conflict: Bool? = nil, detail: String? = nil, limitMicros: Int? = nil, overLimit: Bool? = nil, readOnly: Bool? = nil, spentMicros: Int? = nil, status: Int, title: String, type: String, version: Int? = nil) {
            self.aiOff = aiOff
            self.busy = busy
            self.conflict = conflict
            self.detail = detail
            self.limitMicros = limitMicros
            self.overLimit = overLimit
            self.readOnly = readOnly
            self.spentMicros = spentMicros
            self.status = status
            self.title = title
            self.type = type
            self.version = version
        }

        enum CodingKeys: String, CodingKey {
            case aiOff = "ai_off"
            case busy
            case conflict
            case detail
            case limitMicros = "limit_micros"
            case overLimit = "over_limit"
            case readOnly = "read_only"
            case spentMicros = "spent_micros"
            case status
            case title
            case type
            case version
        }
    }

    public struct DraftDiscarded: Codable, Hashable, Sendable {
        public var deleted: Bool
        public var ok: Bool

        public init(deleted: Bool, ok: Bool) {
            self.deleted = deleted
            self.ok = ok
        }

        /// The keys it names, and those it requires, which a union's decoding goes by.
        static let named: Set<String> = ["deleted", "ok"]
        static let required: Set<String> = ["deleted", "ok"]
    }

    public struct DraftFile: Codable, Hashable, Sendable {
        public var b64: String
        public var cid: String?
        public var contentType: String
        public var filename: String
        public var size: Int

        public init(b64: String, cid: String? = nil, contentType: String, filename: String, size: Int) {
            self.b64 = b64
            self.cid = cid
            self.contentType = contentType
            self.filename = filename
            self.size = size
        }

        enum CodingKeys: String, CodingKey {
            case b64
            case cid
            case contentType = "content_type"
            case filename
            case size
        }
    }

    public struct DraftNotes: Codable, Hashable, Sendable {
        public var bccDropped: Int
        public var inline: Int
        public var plain: Bool

        public init(bccDropped: Int, inline: Int, plain: Bool) {
            self.bccDropped = bccDropped
            self.inline = inline
            self.plain = plain
        }

        enum CodingKeys: String, CodingKey {
            case bccDropped = "bcc_dropped"
            case inline
            case plain
        }
    }

    public struct DraftOpened: Codable, Hashable, Sendable {
        public var account: String
        public var bcc: [String]
        public var cc: [String]
        public var draftId: Int
        public var files: [DraftFile]
        public var inReplyTo: String
        public var mode: ComposeMode
        public var notes: DraftNotes
        public var ok: Bool
        public var originalFrom: String
        public var originalKey: Int?
        public var participants: [String]
        public var references: String
        public var replyTo: String
        public var split: String
        public var subject: String
        public var tailText: String
        public var text: String
        public var to: [String]
        public var version: Int

        public init(account: String, bcc: [String], cc: [String], draftId: Int, files: [DraftFile], inReplyTo: String, mode: ComposeMode, notes: DraftNotes, ok: Bool, originalFrom: String, originalKey: Int? = nil, participants: [String], references: String, replyTo: String, split: String, subject: String, tailText: String, text: String, to: [String], version: Int) {
            self.account = account
            self.bcc = bcc
            self.cc = cc
            self.draftId = draftId
            self.files = files
            self.inReplyTo = inReplyTo
            self.mode = mode
            self.notes = notes
            self.ok = ok
            self.originalFrom = originalFrom
            self.originalKey = originalKey
            self.participants = participants
            self.references = references
            self.replyTo = replyTo
            self.split = split
            self.subject = subject
            self.tailText = tailText
            self.text = text
            self.to = to
            self.version = version
        }

        enum CodingKeys: String, CodingKey {
            case account
            case bcc
            case cc
            case draftId = "draft_id"
            case files
            case inReplyTo = "in_reply_to"
            case mode
            case notes
            case ok
            case originalFrom = "original_from"
            case originalKey = "original_key"
            case participants
            case references
            case replyTo = "reply_to"
            case split
            case subject
            case tailText = "tail_text"
            case text
            case to
            case version
        }
    }

    /// What to reply to as a summary names it, and `account` says who replies, which may not be whose mail it is.
    public struct DraftReplyBody: Codable, Hashable, Sendable {
        public var account: String?
        public var folder: String?
        public var instruction: String?
        public var key: Int?
        public var threadId: String?
        public var tone: DraftTone?
        public var uid: Int?

        public init(account: String? = nil, folder: String? = nil, instruction: String? = nil, key: Int? = nil, threadId: String? = nil, tone: DraftTone? = nil, uid: Int? = nil) {
            self.account = account
            self.folder = folder
            self.instruction = instruction
            self.key = key
            self.threadId = threadId
            self.tone = tone
            self.uid = uid
        }

        enum CodingKeys: String, CodingKey {
            case account
            case folder
            case instruction
            case key
            case threadId = "thread_id"
            case tone
            case uid
        }
    }

    public struct DraftSaved: Codable, Hashable, Sendable {
        public var draftId: Int
        public var files: [DraftFile]?
        public var messageId: String
        public var ok: Bool
        public var pushed: Bool
        public var resume: HandedOver?
        public var uid: Int?
        public var version: Int

        public init(draftId: Int, files: [DraftFile]? = nil, messageId: String, ok: Bool, pushed: Bool, resume: HandedOver? = nil, uid: Int? = nil, version: Int) {
            self.draftId = draftId
            self.files = files
            self.messageId = messageId
            self.ok = ok
            self.pushed = pushed
            self.resume = resume
            self.uid = uid
            self.version = version
        }

        enum CodingKeys: String, CodingKey {
            case draftId = "draft_id"
            case files
            case messageId = "message_id"
            case ok
            case pushed
            case resume
            case uid
            case version
        }

        /// The keys it names, and those it requires, which a union's decoding goes by.
        static let named: Set<String> = ["draft_id", "files", "message_id", "ok", "pushed", "resume", "uid", "version"]
        static let required: Set<String> = ["draft_id", "message_id", "ok", "pushed", "version"]
    }

    public enum DraftTone: String, Codable, Hashable, Sendable, CaseIterable {
        case brief
        case balanced
        case warm
        case formal
    }

    public struct DraftedReply: Codable, Hashable, Sendable {
        public var body: String
        public var model: String
        public var ok: Bool
        public var tone: String

        public init(body: String, model: String, ok: Bool, tone: String) {
            self.body = body
            self.model = model
            self.ok = ok
            self.tone = tone
        }
    }

    /// An event someone confirmed from the message `key` names, each field as they left it. Which are missing or won't do is
    /// the use case's to word.
    public struct EventBody: Codable, Hashable, Sendable {
        public var end: String?
        public var folder: String?
        public var key: Int?
        public var place: String?
        public var start: String?
        public var title: String?
        public var uid: Int?

        public init(end: String? = nil, folder: String? = nil, key: Int? = nil, place: String? = nil, start: String? = nil, title: String? = nil, uid: Int? = nil) {
            self.end = end
            self.folder = folder
            self.key = key
            self.place = place
            self.start = start
            self.title = title
            self.uid = uid
        }
    }

    /// A METHOD:PUBLISH calendar, as text, for the page to save.
    public struct EventFile: Codable, Hashable, Sendable {
        public var filename: String
        public var ics: String
        public var ok: Bool

        public init(filename: String, ics: String, ok: Bool) {
            self.filename = filename
            self.ics = ics
            self.ok = ok
        }
    }

    /// A plan's confirm that came too late, or came twice.
    public struct Expired: Codable, Hashable, Sendable {
        public var aiOff: Bool?
        public var busy: Bool?
        public var detail: String?
        public var expired: Bool
        public var limitMicros: Int?
        public var overLimit: Bool?
        public var readOnly: Bool?
        public var spentMicros: Int?
        public var status: Int
        public var title: String
        public var type: String

        public init(aiOff: Bool? = nil, busy: Bool? = nil, detail: String? = nil, expired: Bool, limitMicros: Int? = nil, overLimit: Bool? = nil, readOnly: Bool? = nil, spentMicros: Int? = nil, status: Int, title: String, type: String) {
            self.aiOff = aiOff
            self.busy = busy
            self.detail = detail
            self.expired = expired
            self.limitMicros = limitMicros
            self.overLimit = overLimit
            self.readOnly = readOnly
            self.spentMicros = spentMicros
            self.status = status
            self.title = title
            self.type = type
        }

        enum CodingKeys: String, CodingKey {
            case aiOff = "ai_off"
            case busy
            case detail
            case expired
            case limitMicros = "limit_micros"
            case overLimit = "over_limit"
            case readOnly = "read_only"
            case spentMicros = "spent_micros"
            case status
            case title
            case type
        }
    }

    public struct ExportGroup: Codable, Hashable, Sendable {
        public var account: String
        public var bodies: Int
        public var folder: String
        public var messages: Int

        public init(account: String, bodies: Int, folder: String, messages: Int) {
            self.account = account
            self.bodies = bodies
            self.folder = folder
            self.messages = messages
        }
    }

    /// `export.plan`: each account's folders, and how many of their messages have a body stored; and how much room
    /// the database takes on this disk, its file and the log of what it hasn't taken in yet, in bytes.
    public struct ExportPlan: Codable, Hashable, Sendable {
        public var bodies: Int
        public var databaseBytes: Int
        public var groups: [ExportGroup]
        public var ok: Bool
        public var total: Int

        public init(bodies: Int, databaseBytes: Int, groups: [ExportGroup], ok: Bool, total: Int) {
            self.bodies = bodies
            self.databaseBytes = databaseBytes
            self.groups = groups
            self.ok = ok
            self.total = total
        }

        enum CodingKeys: String, CodingKey {
            case bodies
            case databaseBytes = "database_bytes"
            case groups
            case ok
            case total
        }
    }

    public struct Extracted: Codable, Hashable, Sendable {
        public var events: [SuggestedEvent]
        public var model: String
        public var ok: Bool
        public var tasks: [SuggestedTask]

        public init(events: [SuggestedEvent], model: String, ok: Bool, tasks: [SuggestedTask]) {
            self.events = events
            self.model = model
            self.ok = ok
            self.tasks = tasks
        }
    }

    /// What any route may answer when it can't (`shared.failure_answer`), or the perimeter when it won't: a problem document
    /// (RFC 9457), as application/problem+json. `type` names the kind of problem, `/problems/<kind>` or `about:blank`, and `detail`
    /// is what a person is told, left out where the page words it. The rest is what the page acts on. A route that says more (a
    /// Problem's `more`) names a subclass of its own for that status, through `answers`.
    public struct Failure: Codable, Hashable, Sendable {
        public var aiOff: Bool?
        public var busy: Bool?
        public var detail: String?
        public var limitMicros: Int?
        public var overLimit: Bool?
        public var readOnly: Bool?
        public var spentMicros: Int?
        public var status: Int
        public var title: String
        public var type: String

        public init(aiOff: Bool? = nil, busy: Bool? = nil, detail: String? = nil, limitMicros: Int? = nil, overLimit: Bool? = nil, readOnly: Bool? = nil, spentMicros: Int? = nil, status: Int, title: String, type: String) {
            self.aiOff = aiOff
            self.busy = busy
            self.detail = detail
            self.limitMicros = limitMicros
            self.overLimit = overLimit
            self.readOnly = readOnly
            self.spentMicros = spentMicros
            self.status = status
            self.title = title
            self.type = type
        }

        enum CodingKeys: String, CodingKey {
            case aiOff = "ai_off"
            case busy
            case detail
            case limitMicros = "limit_micros"
            case overLimit = "over_limit"
            case readOnly = "read_only"
            case spentMicros = "spent_micros"
            case status
            case title
            case type
        }
    }

    public struct FeatureCost: Codable, Hashable, Sendable {
        public var calls: Int
        public var costMicros: Int

        public init(calls: Int, costMicros: Int) {
            self.calls = calls
            self.costMicros = costMicros
        }

        enum CodingKeys: String, CodingKey {
            case calls
            case costMicros = "cost_micros"
        }
    }

    /// What one feature's calls cost, by the name a person knows it by (`spending.label`).
    public struct FeatureSpent: Codable, Hashable, Sendable {
        public var calls: Int
        public var costMicros: Int
        public var label: String

        public init(calls: Int, costMicros: Int, label: String) {
            self.calls = calls
            self.costMicros = costMicros
            self.label = label
        }

        enum CodingKeys: String, CodingKey {
            case calls
            case costMicros = "cost_micros"
            case label
        }
    }

    public struct Filed: Codable, Hashable, Sendable {
        public var msg: String
        public var ok: Bool
        public var status: String?
        public var undoable: Bool

        public init(msg: String, ok: Bool, status: String? = nil, undoable: Bool) {
            self.msg = msg
            self.ok = ok
            self.status = status
            self.undoable = undoable
        }
    }

    public enum FilingVerb: String, Codable, Hashable, Sendable, CaseIterable {
        case archive
        case trash
        case junk
    }

    public struct FilterChange: Codable, Hashable, Sendable {
        public var id: String?
        public var q: String?
        public var read: Bool?
        public var star: Bool?
        public var verb: FilterVerb?

        public init(id: String? = nil, q: String? = nil, read: Bool? = nil, star: Bool? = nil, verb: FilterVerb? = nil) {
            self.id = id
            self.q = q
            self.read = read
            self.star = star
            self.verb = verb
        }
    }

    public enum FilterVerb: String, Codable, Hashable, Sendable, CaseIterable {
        case create
        case update
        case delete
    }

    public struct Filters: Codable, Hashable, Sendable {
        public var filters: [MailFilter]
        public var ok: Bool

        public init(filters: [MailFilter], ok: Bool) {
            self.filters = filters
            self.ok = ok
        }
    }

    public struct FlagBody: Codable, Hashable, Sendable {
        public var flag: FlagName?
        public var folder: String
        public var key: Int
        public var mutationId: String?
        public var on: Bool?
        public var uid: Int?

        public init(flag: FlagName? = nil, folder: String, key: Int, mutationId: String? = nil, on: Bool? = nil, uid: Int? = nil) {
            self.flag = flag
            self.folder = folder
            self.key = key
            self.mutationId = mutationId
            self.on = on
            self.uid = uid
        }

        enum CodingKeys: String, CodingKey {
            case flag
            case folder
            case key
            case mutationId = "mutation_id"
            case on
            case uid
        }
    }

    public enum FlagName: String, Codable, Hashable, Sendable, CaseIterable {
        case flagged
        case seen
    }

    public struct FolderChanged: Codable, Hashable, Sendable {
        public var folder: String
        public var ok: Bool

        public init(folder: String, ok: Bool) {
            self.folder = folder
            self.ok = ok
        }
    }

    public struct FolderDeleted: Codable, Hashable, Sendable {
        public var ok: Bool
        public var trashed: Int

        public init(ok: Bool, trashed: Int) {
            self.ok = ok
            self.trashed = trashed
        }
    }

    public struct FolderHoldsMail: Codable, Hashable, Sendable {
        public var aiOff: Bool?
        public var busy: Bool?
        public var detail: String?
        public var limitMicros: Int?
        public var messages: Int?
        public var overLimit: Bool?
        public var readOnly: Bool?
        public var spentMicros: Int?
        public var status: Int
        public var title: String
        public var toTrash: Bool?
        public var trashed: Int?
        public var type: String

        public init(aiOff: Bool? = nil, busy: Bool? = nil, detail: String? = nil, limitMicros: Int? = nil, messages: Int? = nil, overLimit: Bool? = nil, readOnly: Bool? = nil, spentMicros: Int? = nil, status: Int, title: String, toTrash: Bool? = nil, trashed: Int? = nil, type: String) {
            self.aiOff = aiOff
            self.busy = busy
            self.detail = detail
            self.limitMicros = limitMicros
            self.messages = messages
            self.overLimit = overLimit
            self.readOnly = readOnly
            self.spentMicros = spentMicros
            self.status = status
            self.title = title
            self.toTrash = toTrash
            self.trashed = trashed
            self.type = type
        }

        enum CodingKeys: String, CodingKey {
            case aiOff = "ai_off"
            case busy
            case detail
            case limitMicros = "limit_micros"
            case messages
            case overLimit = "over_limit"
            case readOnly = "read_only"
            case spentMicros = "spent_micros"
            case status
            case title
            case toTrash = "to_trash"
            case trashed
            case type
        }
    }

    public struct FolderListing: Codable, Hashable, Sendable {
        public var account: String?
        public var catchingUp: Bool?
        public var folder: String
        public var grouped: Bool
        public var hasMore: Bool
        public var items: [FolderRow]
        public var offset: Int
        public var ok: Bool
        public var syncFailed: Bool?
        public var syncing: Bool?
        public var total: Int

        public init(account: String? = nil, catchingUp: Bool? = nil, folder: String, grouped: Bool, hasMore: Bool, items: [FolderRow], offset: Int, ok: Bool, syncFailed: Bool? = nil, syncing: Bool? = nil, total: Int) {
            self.account = account
            self.catchingUp = catchingUp
            self.folder = folder
            self.grouped = grouped
            self.hasMore = hasMore
            self.items = items
            self.offset = offset
            self.ok = ok
            self.syncFailed = syncFailed
            self.syncing = syncing
            self.total = total
        }

        enum CodingKeys: String, CodingKey {
            case account
            case catchingUp = "catching_up"
            case folder
            case grouped
            case hasMore = "has_more"
            case items
            case offset
            case ok
            case syncFailed = "sync_failed"
            case syncing
            case total
        }

        /// The keys it names, and those it requires, which a union's decoding goes by.
        static let named: Set<String> = ["account", "catching_up", "folder", "grouped", "has_more", "items", "offset", "ok", "sync_failed", "syncing", "total"]
        static let required: Set<String> = ["account", "folder", "grouped", "has_more", "items", "offset", "ok", "total"]
    }

    public struct FolderRow: Codable, Hashable, Sendable {
        public var account: String
        public var action: String
        public var bucket: String?
        public var category: String?
        public var commitmentWorthy: Bool
        public var count: Int?
        public var flagged: Bool
        public var folder: String
        public var fromAddr: String?
        public var fromName: String?
        public var hasAttach: Bool
        public var importance: Int?
        public var isJunk: Bool
        public var key: Int
        public var needsYou: Bool
        public var replyWorthy: Bool
        public var safeToFile: Bool
        public var seen: Bool
        public var sentTo: [Addressee]
        public var sentToMore: Int
        public var snippet: String
        public var subject: String
        public var summary: String
        public var threadCount: Int?
        public var threadId: String?
        public var ts: Int?
        public var uid: Int
        public var when: String

        public init(account: String, action: String, bucket: String? = nil, category: String? = nil, commitmentWorthy: Bool, count: Int? = nil, flagged: Bool, folder: String, fromAddr: String? = nil, fromName: String? = nil, hasAttach: Bool, importance: Int? = nil, isJunk: Bool, key: Int, needsYou: Bool, replyWorthy: Bool, safeToFile: Bool, seen: Bool, sentTo: [Addressee], sentToMore: Int, snippet: String, subject: String, summary: String, threadCount: Int? = nil, threadId: String? = nil, ts: Int? = nil, uid: Int, when: String) {
            self.account = account
            self.action = action
            self.bucket = bucket
            self.category = category
            self.commitmentWorthy = commitmentWorthy
            self.count = count
            self.flagged = flagged
            self.folder = folder
            self.fromAddr = fromAddr
            self.fromName = fromName
            self.hasAttach = hasAttach
            self.importance = importance
            self.isJunk = isJunk
            self.key = key
            self.needsYou = needsYou
            self.replyWorthy = replyWorthy
            self.safeToFile = safeToFile
            self.seen = seen
            self.sentTo = sentTo
            self.sentToMore = sentToMore
            self.snippet = snippet
            self.subject = subject
            self.summary = summary
            self.threadCount = threadCount
            self.threadId = threadId
            self.ts = ts
            self.uid = uid
            self.when = when
        }

        enum CodingKeys: String, CodingKey {
            case account
            case action
            case bucket
            case category
            case commitmentWorthy = "commitment_worthy"
            case count
            case flagged
            case folder
            case fromAddr = "from_addr"
            case fromName = "from_name"
            case hasAttach = "has_attach"
            case importance
            case isJunk = "is_junk"
            case key
            case needsYou = "needs_you"
            case replyWorthy = "reply_worthy"
            case safeToFile = "safe_to_file"
            case seen
            case sentTo = "sent_to"
            case sentToMore = "sent_to_more"
            case snippet
            case subject
            case summary
            case threadCount = "thread_count"
            case threadId = "thread_id"
            case ts
            case uid
            case when
        }
    }

    public struct Folders: Codable, Hashable, Sendable {
        public var account: String
        public var folders: [RailFolder]
        public var muted: Int
        public var ok: Bool
        public var replylater: Int
        public var snoozed: Int
        public var waiting: Int

        public init(account: String, folders: [RailFolder], muted: Int, ok: Bool, replylater: Int, snoozed: Int, waiting: Int) {
            self.account = account
            self.folders = folders
            self.muted = muted
            self.ok = ok
            self.replylater = replylater
            self.snoozed = snoozed
            self.waiting = waiting
        }
    }

    public struct FoldersRefused: Codable, Hashable, Sendable {
        public var aiOff: Bool?
        public var busy: Bool?
        public var detail: String?
        public var folders: [RailFolder]?
        public var limitMicros: Int?
        public var overLimit: Bool?
        public var readOnly: Bool?
        public var spentMicros: Int?
        public var status: Int
        public var title: String
        public var type: String

        public init(aiOff: Bool? = nil, busy: Bool? = nil, detail: String? = nil, folders: [RailFolder]? = nil, limitMicros: Int? = nil, overLimit: Bool? = nil, readOnly: Bool? = nil, spentMicros: Int? = nil, status: Int, title: String, type: String) {
            self.aiOff = aiOff
            self.busy = busy
            self.detail = detail
            self.folders = folders
            self.limitMicros = limitMicros
            self.overLimit = overLimit
            self.readOnly = readOnly
            self.spentMicros = spentMicros
            self.status = status
            self.title = title
            self.type = type
        }

        enum CodingKeys: String, CodingKey {
            case aiOff = "ai_off"
            case busy
            case detail
            case folders
            case limitMicros = "limit_micros"
            case overLimit = "over_limit"
            case readOnly = "read_only"
            case spentMicros = "spent_micros"
            case status
            case title
            case type
        }
    }

    /// `CostGovernor.snapshot`. A spending the ledger can't say is None.
    public struct Governor: Codable, Hashable, Sendable {
        public var breaker: [String]
        public var interactiveRunning: Int
        public var limitMicros: Int
        public var reservedMicros: Int?
        public var sinceInteractive: Double?
        public var sinceSeen: Double
        public var spentTodayMicros: Int?

        public init(breaker: [String], interactiveRunning: Int, limitMicros: Int, reservedMicros: Int? = nil, sinceInteractive: Double? = nil, sinceSeen: Double, spentTodayMicros: Int? = nil) {
            self.breaker = breaker
            self.interactiveRunning = interactiveRunning
            self.limitMicros = limitMicros
            self.reservedMicros = reservedMicros
            self.sinceInteractive = sinceInteractive
            self.sinceSeen = sinceSeen
            self.spentTodayMicros = spentTodayMicros
        }

        enum CodingKeys: String, CodingKey {
            case breaker
            case interactiveRunning = "interactive_running"
            case limitMicros = "limit_micros"
            case reservedMicros = "reserved_micros"
            case sinceInteractive = "since_interactive"
            case sinceSeen = "since_seen"
            case spentTodayMicros = "spent_today_micros"
        }
    }

    public struct HandedOver: Codable, Hashable, Sendable {
        public var inReplyTo: String
        public var references: String
        public var tailText: String

        public init(inReplyTo: String, references: String, tailText: String) {
            self.inReplyTo = inReplyTo
            self.references = references
            self.tailText = tailText
        }

        enum CodingKeys: String, CodingKey {
            case inReplyTo = "in_reply_to"
            case references
            case tailText = "tail_text"
        }

        /// The keys it names, and those it requires, which a union's decoding goes by.
        static let named: Set<String> = ["in_reply_to", "references", "tail_text"]
        static let required: Set<String> = ["in_reply_to", "references", "tail_text"]
    }

    /// What a browser kept under its old keys, once, in the block's words. Any value: one no preference can be is left out, since
    /// nobody asked for it to be refused.
    public struct HeldPreferences: Codable, Hashable, Sendable {
        public var held: [String: JSONValue]

        public init(held: [String: JSONValue]) {
            self.held = held
        }
    }

    public struct Idle: Codable, Hashable, Sendable {
        public var accounts: Int
        public var running: Int

        public init(accounts: Int, running: Int) {
            self.accounts = accounts
            self.running = running
        }
    }

    public struct InboxesListing: Codable, Hashable, Sendable {
        public var folder: String
        public var grouped: Bool
        public var hasMore: Bool
        public var items: [FolderRow]
        public var offset: Int
        public var ok: Bool
        public var syncing: Bool?
        public var total: Int

        public init(folder: String, grouped: Bool, hasMore: Bool, items: [FolderRow], offset: Int, ok: Bool, syncing: Bool? = nil, total: Int) {
            self.folder = folder
            self.grouped = grouped
            self.hasMore = hasMore
            self.items = items
            self.offset = offset
            self.ok = ok
            self.syncing = syncing
            self.total = total
        }

        enum CodingKeys: String, CodingKey {
            case folder
            case grouped
            case hasMore = "has_more"
            case items
            case offset
            case ok
            case syncing
            case total
        }
    }

    public struct Invite: Codable, Hashable, Sendable {
        public var allDay: Bool
        public var attendees: [Party]
        public var description: String
        public var endIso: String
        public var exdates: [String]
        public var location: String
        public var method: String
        public var organizer: Party
        public var rdates: [String]?
        public var recurrence: String
        public var recurrenceId: String
        public var rrule: String
        public var sequence: Int
        public var startIso: String
        public var status: String
        public var summary: String
        public var thisAndFuture: Bool?
        public var tzid: String
        public var uid: String
        public var when: String

        public init(allDay: Bool, attendees: [Party], description: String, endIso: String, exdates: [String], location: String, method: String, organizer: Party, rdates: [String]? = nil, recurrence: String, recurrenceId: String, rrule: String, sequence: Int, startIso: String, status: String, summary: String, thisAndFuture: Bool? = nil, tzid: String, uid: String, when: String) {
            self.allDay = allDay
            self.attendees = attendees
            self.description = description
            self.endIso = endIso
            self.exdates = exdates
            self.location = location
            self.method = method
            self.organizer = organizer
            self.rdates = rdates
            self.recurrence = recurrence
            self.recurrenceId = recurrenceId
            self.rrule = rrule
            self.sequence = sequence
            self.startIso = startIso
            self.status = status
            self.summary = summary
            self.thisAndFuture = thisAndFuture
            self.tzid = tzid
            self.uid = uid
            self.when = when
        }

        enum CodingKeys: String, CodingKey {
            case allDay = "all_day"
            case attendees
            case description
            case endIso = "end_iso"
            case exdates
            case location
            case method
            case organizer
            case rdates
            case recurrence
            case recurrenceId = "recurrence_id"
            case rrule
            case sequence
            case startIso = "start_iso"
            case status
            case summary
            case thisAndFuture = "this_and_future"
            case tzid
            case uid
            case when
        }

        /// The keys it names, and those it requires, which a union's decoding goes by.
        static let named: Set<String> = ["all_day", "attendees", "description", "end_iso", "exdates", "location", "method", "organizer", "rdates", "recurrence", "recurrence_id", "rrule", "sequence", "start_iso", "status", "summary", "this_and_future", "tzid", "uid", "when"]
        static let required: Set<String> = ["all_day", "attendees", "description", "end_iso", "exdates", "location", "method", "organizer", "recurrence", "recurrence_id", "rrule", "sequence", "start_iso", "status", "summary", "tzid", "uid", "when"]
    }

    /// `Scheduler.snapshot`. Times are wall-clock seconds, and `next_at` is None while a round runs or no lane does.
    public struct Job: Codable, Hashable, Sendable {
        public var every: Double
        public var failures: Int
        public var lastError: String?
        public var lastStarted: Double?
        public var lastTook: Double?
        public var name: String
        public var nextAt: Double?
        public var running: Bool
        public var runs: Int

        public init(every: Double, failures: Int, lastError: String? = nil, lastStarted: Double? = nil, lastTook: Double? = nil, name: String, nextAt: Double? = nil, running: Bool, runs: Int) {
            self.every = every
            self.failures = failures
            self.lastError = lastError
            self.lastStarted = lastStarted
            self.lastTook = lastTook
            self.name = name
            self.nextAt = nextAt
            self.running = running
            self.runs = runs
        }

        enum CodingKeys: String, CodingKey {
            case every
            case failures
            case lastError = "last_error"
            case lastStarted = "last_started"
            case lastTook = "last_took"
            case name
            case nextAt = "next_at"
            case running
            case runs
        }
    }

    public struct Jobs: Codable, Hashable, Sendable {
        public var generated: String
        public var jobs: [Job]
        public var ok: Bool
        public var profile: String

        public init(generated: String, jobs: [Job], ok: Bool, profile: String) {
            self.generated = generated
            self.jobs = jobs
            self.ok = ok
            self.profile = profile
        }
    }

    /// Whether one is set: never what it is, how long, or any part of it.
    public struct Kept: Codable, Hashable, Sendable {
        public var ok: Bool
        public var set: Bool

        public init(ok: Bool, set: Bool) {
            self.ok = ok
            self.set = set
        }
    }

    /// An AI provider's API key, as typed or pasted.
    public struct KeyBody: Codable, Hashable, Sendable {
        public var key: String
        public var provider: AiProvider

        public init(key: String, provider: AiProvider) {
            self.key = key
            self.provider = provider
        }
    }

    /// Each AI provider whose key the config names, in the providers' order: never a key, how long, or any part of it.
    public struct KeysKept: Codable, Hashable, Sendable {
        public var kept: [AiProvider]
        public var ok: Bool

        public init(kept: [AiProvider], ok: Bool) {
            self.kept = kept
            self.ok = ok
        }
    }

    public struct Known: Codable, Hashable, Sendable {
        public var bad: [String]
        public var items: [String: KnownVerdict]
        public var ok: Bool
        public var thin: Bool

        public init(bad: [String], items: [String: KnownVerdict], ok: Bool, thin: Bool) {
            self.bad = bad
            self.items = items
            self.ok = ok
            self.thin = thin
        }
    }

    public struct KnownBody: Codable, Hashable, Sendable {
        public var addrs: [JSONValue]?

        public init(addrs: [JSONValue]? = nil) {
            self.addrs = addrs
        }
    }

    public struct KnownVerdict: Codable, Hashable, Sendable {
        public var known: Bool
        public var near: String?
        public var self_: Bool

        public init(known: Bool, near: String? = nil, self_: Bool) {
            self.known = known
            self.near = near
            self.self_ = self_
        }

        enum CodingKeys: String, CodingKey {
            case known
            case near
            case self_ = "self"
        }
    }

    public struct Label: Codable, Hashable, Sendable {
        public var count: Int
        public var name: String

        public init(count: Int, name: String) {
            self.count = count
            self.name = name
        }
    }

    public struct Labels: Codable, Hashable, Sendable {
        public var labels: [Label]
        public var ok: Bool

        public init(labels: [Label], ok: Bool) {
            self.labels = labels
            self.ok = ok
        }
    }

    /// A page a person is sent to (a provider's, where its app password is made), and the words of its link.
    public struct Link: Codable, Hashable, Sendable {
        public var url: String
        public var words: String

        public init(url: String, words: String) {
            self.url = url
            self.words = words
        }

        /// The keys it names, and those it requires, which a union's decoding goes by.
        static let named: Set<String> = ["url", "words"]
        static let required: Set<String> = ["url", "words"]
    }

    public struct Listed: Codable, Hashable, Sendable {
        public var accounts: [AccountRow]
        public var generated: String
        public var ok: Bool
        public var signatureDefault: String

        public init(accounts: [AccountRow], generated: String, ok: Bool, signatureDefault: String) {
            self.accounts = accounts
            self.generated = generated
            self.ok = ok
            self.signatureDefault = signatureDefault
        }

        enum CodingKeys: String, CodingKey {
            case accounts
            case generated
            case ok
            case signatureDefault = "signature_default"
        }
    }

    public struct ListedCopy: Codable, Hashable, Sendable {
        public var ok: Bool
        public var row: FolderRow

        public init(ok: Bool, row: FolderRow) {
            self.ok = ok
            self.row = row
        }
    }

    public struct ListedView: Codable, Hashable, Sendable {
        public var builtin: Bool
        public var dynamic: String?
        public var id: String
        public var name: String
        public var q: String

        public init(builtin: Bool, dynamic: String? = nil, id: String, name: String, q: String) {
            self.builtin = builtin
            self.dynamic = dynamic
            self.id = id
            self.name = name
            self.q = q
        }
    }

    /// A model server on this Mac that answered: its name, the base URL a model on this Mac is reached at there, and the
    /// models it lists that a person may choose, sorted.
    public struct LocalServer: Codable, Hashable, Sendable {
        public var models: [String]
        public var name: String
        public var url: String

        public init(models: [String], name: String, url: String) {
            self.models = models
            self.name = name
            self.url = url
        }
    }

    /// The model servers on this Mac that answered, in the order they are known; none is an empty list.
    public struct LocalServers: Codable, Hashable, Sendable {
        public var ok: Bool
        public var servers: [LocalServer]

        public init(ok: Bool, servers: [LocalServer]) {
            self.ok = ok
            self.servers = servers
        }
    }

    public struct LoggedIn: Codable, Hashable, Sendable {
        public var connect: Bool
        public var detail: String
        public var email: String
        public var ms: Int
        public var ok: Bool

        public init(connect: Bool, detail: String, email: String, ms: Int, ok: Bool) {
            self.connect = connect
            self.detail = detail
            self.email = email
            self.ms = ms
            self.ok = ok
        }

        /// The keys it names, and those it requires, which a union's decoding goes by.
        static let named: Set<String> = ["connect", "detail", "email", "ms", "ok"]
        static let required: Set<String> = ["connect", "detail", "email", "ms", "ok"]
    }

    public enum MailColors: String, Codable, Hashable, Sendable, CaseIterable {
        case auto
        case dark
        case original
    }

    /// What to do with new mail that fits a search.
    public struct MailFilter: Codable, Hashable, Sendable {
        public var id: String
        public var q: String
        public var read: Bool
        public var star: Bool

        public init(id: String, q: String, read: Bool, star: Bool) {
            self.id = id
            self.q = q
            self.read = read
            self.star = star
        }
    }

    public struct ManyFiled: Codable, Hashable, Sendable {
        public var count: Int
        public var failed: Int
        public var filed: [Copy]
        public var msg: String
        public var ok: Bool
        public var queued: Int
        public var undoable: Bool

        public init(count: Int, failed: Int, filed: [Copy], msg: String, ok: Bool, queued: Int, undoable: Bool) {
            self.count = count
            self.failed = failed
            self.filed = filed
            self.msg = msg
            self.ok = ok
            self.queued = queued
            self.undoable = undoable
        }
    }

    public struct MessageOpened: Codable, Hashable, Sendable {
        public var account: String
        public var attachments: [AttachmentPart]
        public var blockedCssUrls: Int
        public var blockedImages: Int
        public var cc: String
        public var date: String
        public var draftId: Int?
        public var draftMode: String?
        public var folder: String
        public var fromAddr: String?
        public var fromName: String?
        public var hasHtml: Bool
        public var html: String?
        public var inReplyTo: String?
        public var invite: Invite?
        public var invitePart: String?
        public var isDraft: Bool?
        public var key: Int
        public var messageId: String?
        public var ok: Bool
        public var references: String?
        public var replyDraft: ReplyDraft?
        public var replyTo: String
        public var sender: SenderFacts?
        public var subject: String?
        public var text: String?
        public var threadId: String?
        public var to: String
        public var uid: Int

        public init(account: String, attachments: [AttachmentPart], blockedCssUrls: Int, blockedImages: Int, cc: String, date: String, draftId: Int? = nil, draftMode: String? = nil, folder: String, fromAddr: String? = nil, fromName: String? = nil, hasHtml: Bool, html: String? = nil, inReplyTo: String? = nil, invite: Invite? = nil, invitePart: String? = nil, isDraft: Bool? = nil, key: Int, messageId: String? = nil, ok: Bool, references: String? = nil, replyDraft: ReplyDraft? = nil, replyTo: String, sender: SenderFacts? = nil, subject: String? = nil, text: String? = nil, threadId: String? = nil, to: String, uid: Int) {
            self.account = account
            self.attachments = attachments
            self.blockedCssUrls = blockedCssUrls
            self.blockedImages = blockedImages
            self.cc = cc
            self.date = date
            self.draftId = draftId
            self.draftMode = draftMode
            self.folder = folder
            self.fromAddr = fromAddr
            self.fromName = fromName
            self.hasHtml = hasHtml
            self.html = html
            self.inReplyTo = inReplyTo
            self.invite = invite
            self.invitePart = invitePart
            self.isDraft = isDraft
            self.key = key
            self.messageId = messageId
            self.ok = ok
            self.references = references
            self.replyDraft = replyDraft
            self.replyTo = replyTo
            self.sender = sender
            self.subject = subject
            self.text = text
            self.threadId = threadId
            self.to = to
            self.uid = uid
        }

        enum CodingKeys: String, CodingKey {
            case account
            case attachments
            case blockedCssUrls = "blocked_css_urls"
            case blockedImages = "blocked_images"
            case cc
            case date
            case draftId = "draft_id"
            case draftMode = "draft_mode"
            case folder
            case fromAddr = "from_addr"
            case fromName = "from_name"
            case hasHtml = "has_html"
            case html
            case inReplyTo = "in_reply_to"
            case invite
            case invitePart = "invite_part"
            case isDraft = "is_draft"
            case key
            case messageId = "message_id"
            case ok
            case references
            case replyDraft = "reply_draft"
            case replyTo = "reply_to"
            case sender
            case subject
            case text
            case threadId = "thread_id"
            case to
            case uid
        }

        /// The keys it names, and those it requires, which a union's decoding goes by.
        static let named: Set<String> = ["account", "attachments", "blocked_css_urls", "blocked_images", "cc", "date", "draft_id", "draft_mode", "folder", "from_addr", "from_name", "has_html", "html", "in_reply_to", "invite", "invite_part", "is_draft", "key", "message_id", "ok", "references", "reply_draft", "reply_to", "sender", "subject", "text", "thread_id", "to", "uid"]
        static let required: Set<String> = ["account", "attachments", "blocked_css_urls", "blocked_images", "cc", "date", "folder", "from_addr", "from_name", "has_html", "html", "in_reply_to", "invite", "invite_part", "key", "message_id", "ok", "references", "reply_to", "sender", "subject", "text", "thread_id", "to", "uid"]
    }

    /// The models a provider lists that a person may choose, sorted, and the one it asks unless they choose (None where they
    /// must).
    public struct ModelList: Codable, Hashable, Sendable {
        public var `default`: String?
        public var models: [String]
        public var ok: Bool

        public init(`default`: String? = nil, models: [String], ok: Bool) {
            self.`default` = `default`
            self.models = models
            self.ok = ok
        }
    }

    public struct MoveBody: Codable, Hashable, Sendable {
        public var folder: String
        public var key: Int
        public var mutationId: String?
        public var uid: Int?
        public var undoGroup: String?

        public init(folder: String, key: Int, mutationId: String? = nil, uid: Int? = nil, undoGroup: String? = nil) {
            self.folder = folder
            self.key = key
            self.mutationId = mutationId
            self.uid = uid
            self.undoGroup = undoGroup
        }

        enum CodingKeys: String, CodingKey {
            case folder
            case key
            case mutationId = "mutation_id"
            case uid
            case undoGroup = "undo_group"
        }
    }

    public struct MoveToBody: Codable, Hashable, Sendable {
        public var folder: String
        public var key: Int
        public var mutationId: String?
        public var to: String
        public var uid: Int?
        public var undoGroup: String?

        public init(folder: String, key: Int, mutationId: String? = nil, to: String, uid: Int? = nil, undoGroup: String? = nil) {
            self.folder = folder
            self.key = key
            self.mutationId = mutationId
            self.to = to
            self.uid = uid
            self.undoGroup = undoGroup
        }

        enum CodingKeys: String, CodingKey {
            case folder
            case key
            case mutationId = "mutation_id"
            case to
            case uid
            case undoGroup = "undo_group"
        }
    }

    public struct MuteBody: Codable, Hashable, Sendable {
        public var account: String?
        public var folder: String?
        public var key: Int?
        public var threadId: String?
        public var uid: Int?
        public var undoGroup: String?

        public init(account: String? = nil, folder: String? = nil, key: Int? = nil, threadId: String? = nil, uid: Int? = nil, undoGroup: String? = nil) {
            self.account = account
            self.folder = folder
            self.key = key
            self.threadId = threadId
            self.uid = uid
            self.undoGroup = undoGroup
        }

        enum CodingKeys: String, CodingKey {
            case account
            case folder
            case key
            case threadId = "thread_id"
            case uid
            case undoGroup = "undo_group"
        }
    }

    public struct MutedListing: Codable, Hashable, Sendable {
        public var account: String?
        public var folder: String
        public var hasMore: Bool
        public var items: [MutedRow]
        public var muted: Bool
        public var offset: Int
        public var ok: Bool
        public var total: Int

        public init(account: String? = nil, folder: String, hasMore: Bool, items: [MutedRow], muted: Bool, offset: Int, ok: Bool, total: Int) {
            self.account = account
            self.folder = folder
            self.hasMore = hasMore
            self.items = items
            self.muted = muted
            self.offset = offset
            self.ok = ok
            self.total = total
        }

        enum CodingKeys: String, CodingKey {
            case account
            case folder
            case hasMore = "has_more"
            case items
            case muted
            case offset
            case ok
            case total
        }

        /// The keys it names, and those it requires, which a union's decoding goes by.
        static let named: Set<String> = ["account", "folder", "has_more", "items", "muted", "offset", "ok", "total"]
        static let required: Set<String> = ["account", "folder", "has_more", "items", "muted", "offset", "ok", "total"]
    }

    public struct MutedRow: Codable, Hashable, Sendable {
        public var account: String
        public var bucket: String?
        public var count: Int
        public var flagged: Bool
        public var folder: String
        public var fromAddr: String?
        public var fromName: String?
        public var hasAttach: Bool
        public var isJunk: Bool
        public var key: Int
        public var muted: Bool
        public var seen: Bool
        public var sentTo: [Addressee]
        public var sentToMore: Int
        public var snippet: String
        public var subject: String
        public var threadId: String?
        public var ts: Int?
        public var uid: Int
        public var when: String

        public init(account: String, bucket: String? = nil, count: Int, flagged: Bool, folder: String, fromAddr: String? = nil, fromName: String? = nil, hasAttach: Bool, isJunk: Bool, key: Int, muted: Bool, seen: Bool, sentTo: [Addressee], sentToMore: Int, snippet: String, subject: String, threadId: String? = nil, ts: Int? = nil, uid: Int, when: String) {
            self.account = account
            self.bucket = bucket
            self.count = count
            self.flagged = flagged
            self.folder = folder
            self.fromAddr = fromAddr
            self.fromName = fromName
            self.hasAttach = hasAttach
            self.isJunk = isJunk
            self.key = key
            self.muted = muted
            self.seen = seen
            self.sentTo = sentTo
            self.sentToMore = sentToMore
            self.snippet = snippet
            self.subject = subject
            self.threadId = threadId
            self.ts = ts
            self.uid = uid
            self.when = when
        }

        enum CodingKeys: String, CodingKey {
            case account
            case bucket
            case count
            case flagged
            case folder
            case fromAddr = "from_addr"
            case fromName = "from_name"
            case hasAttach = "has_attach"
            case isJunk = "is_junk"
            case key
            case muted
            case seen
            case sentTo = "sent_to"
            case sentToMore = "sent_to_more"
            case snippet
            case subject
            case threadId = "thread_id"
            case ts
            case uid
            case when
        }
    }

    /// A message by its key, and the copy the page has of it by that key, its folder and its uid there (`mailmind.http.named`).
    /// A route that acts on the message takes that copy where there is one, else another.
    public struct NamesAMessage: Codable, Hashable, Sendable {
        public var folder: String?
        public var key: Int?
        public var uid: Int?

        public init(folder: String? = nil, key: Int? = nil, uid: Int? = nil) {
            self.folder = folder
            self.key = key
            self.uid = uid
        }
    }

    public struct NamesAPending: Codable, Hashable, Sendable {
        public var id: Int

        public init(id: Int) {
            self.id = id
        }
    }

    public struct NamesASend: Codable, Hashable, Sendable {
        public var id: Int

        public init(id: Int) {
            self.id = id
        }
    }

    /// A thread, by its account and id, or by a message of it (`Threads.target`), its key and the folder of the copy the
    /// page has.
    public struct NamesAThread: Codable, Hashable, Sendable {
        public var account: String?
        public var folder: String?
        public var key: Int?
        public var threadId: String?
        public var uid: Int?

        public init(account: String? = nil, folder: String? = nil, key: Int? = nil, threadId: String? = nil, uid: Int? = nil) {
            self.account = account
            self.folder = folder
            self.key = key
            self.threadId = threadId
            self.uid = uid
        }

        enum CodingKeys: String, CodingKey {
            case account
            case folder
            case key
            case threadId = "thread_id"
            case uid
        }
    }

    public struct NamesAnAccount: Codable, Hashable, Sendable {
        public var email: String?

        public init(email: String? = nil) {
            self.email = email
        }
    }

    /// A whole thread, by its account and id, else the copies named by key and folder (`snooze.named_thread`, `named_pks`):
    /// `copies`, or the one `key` and `folder` name. More copies than BULK_CAP is the `bad request`, never the first of them: the
    /// page sends more as more requests, under one undo group.
    public struct NamesThreadOrMessages: Codable, Hashable, Sendable {
        public var account: String?
        public var copies: [ACopy]?
        public var folder: String?
        public var key: Int?
        public var threadId: String?
        public var uid: Int?

        public init(account: String? = nil, copies: [ACopy]? = nil, folder: String? = nil, key: Int? = nil, threadId: String? = nil, uid: Int? = nil) {
            self.account = account
            self.copies = copies
            self.folder = folder
            self.key = key
            self.threadId = threadId
            self.uid = uid
        }

        enum CodingKeys: String, CodingKey {
            case account
            case copies
            case folder
            case key
            case threadId = "thread_id"
            case uid
        }
    }

    public struct NewFolderBody: Codable, Hashable, Sendable {
        public var account: String?
        public var name: String?

        public init(account: String? = nil, name: String? = nil) {
            self.account = account
            self.name = name
        }
    }

    /// A POST that reads nothing of its body, which must still be a JSON object.
    public struct NoBody: Codable, Hashable, Sendable {
        public init() {}
    }

    public struct NoisySender: Codable, Hashable, Sendable {
        public var accounts: [SenderAccount]
        public var bucket: String
        public var count: Int
        public var domain: String
        public var fromAddr: String
        public var fromName: String
        public var lastSeen: String
        public var luLikely: Bool
        public var noiseScore: Double
        public var unseen: Int
        public var unseenRatio: Double

        public init(accounts: [SenderAccount], bucket: String, count: Int, domain: String, fromAddr: String, fromName: String, lastSeen: String, luLikely: Bool, noiseScore: Double, unseen: Int, unseenRatio: Double) {
            self.accounts = accounts
            self.bucket = bucket
            self.count = count
            self.domain = domain
            self.fromAddr = fromAddr
            self.fromName = fromName
            self.lastSeen = lastSeen
            self.luLikely = luLikely
            self.noiseScore = noiseScore
            self.unseen = unseen
            self.unseenRatio = unseenRatio
        }

        enum CodingKeys: String, CodingKey {
            case accounts
            case bucket
            case count
            case domain
            case fromAddr = "from_addr"
            case fromName = "from_name"
            case lastSeen = "last_seen"
            case luLikely = "lu_likely"
            case noiseScore = "noise_score"
            case unseen
            case unseenRatio = "unseen_ratio"
        }
    }

    public struct NoisySenders: Codable, Hashable, Sendable {
        public var generated: String
        public var ok: Bool
        public var senders: [NoisySender]

        public init(generated: String, ok: Bool, senders: [NoisySender]) {
            self.generated = generated
            self.ok = ok
            self.senders = senders
        }
    }

    public struct NothingAsked: Codable, Hashable, Sendable {
        public var hasMore: Bool
        public var items: [SearchHit]
        public var ok: Bool
        public var query: String
        public var total: Int

        public init(hasMore: Bool, items: [SearchHit], ok: Bool, query: String, total: Int) {
            self.hasMore = hasMore
            self.items = items
            self.ok = ok
            self.query = query
            self.total = total
        }

        enum CodingKeys: String, CodingKey {
            case hasMore = "has_more"
            case items
            case ok
            case query
            case total
        }

        /// The keys it names, and those it requires, which a union's decoding goes by.
        static let named: Set<String> = ["has_more", "items", "ok", "query", "total"]
        static let required: Set<String> = ["has_more", "items", "ok", "query", "total"]
    }

    public struct NothingToSave: Codable, Hashable, Sendable {
        public var draftId: Int?
        public var empty: Bool
        public var ok: Bool

        public init(draftId: Int? = nil, empty: Bool, ok: Bool) {
            self.draftId = draftId
            self.empty = empty
            self.ok = ok
        }

        enum CodingKeys: String, CodingKey {
            case draftId = "draft_id"
            case empty
            case ok
        }

        /// The keys it names, and those it requires, which a union's decoding goes by.
        static let named: Set<String> = ["draft_id", "empty", "ok"]
        static let required: Set<String> = ["draft_id", "empty", "ok"]
    }

    /// The stack is empty: any other 404 says what `Failure` says.
    public struct NothingToUndo: Codable, Hashable, Sendable {
        public var aiOff: Bool?
        public var busy: Bool?
        public var detail: String?
        public var empty: Bool?
        public var limitMicros: Int?
        public var overLimit: Bool?
        public var readOnly: Bool?
        public var spentMicros: Int?
        public var status: Int
        public var title: String
        public var type: String

        public init(aiOff: Bool? = nil, busy: Bool? = nil, detail: String? = nil, empty: Bool? = nil, limitMicros: Int? = nil, overLimit: Bool? = nil, readOnly: Bool? = nil, spentMicros: Int? = nil, status: Int, title: String, type: String) {
            self.aiOff = aiOff
            self.busy = busy
            self.detail = detail
            self.empty = empty
            self.limitMicros = limitMicros
            self.overLimit = overLimit
            self.readOnly = readOnly
            self.spentMicros = spentMicros
            self.status = status
            self.title = title
            self.type = type
        }

        enum CodingKeys: String, CodingKey {
            case aiOff = "ai_off"
            case busy
            case detail
            case empty
            case limitMicros = "limit_micros"
            case overLimit = "over_limit"
            case readOnly = "read_only"
            case spentMicros = "spent_micros"
            case status
            case title
            case type
        }
    }

    public struct OnThread: Codable, Hashable, Sendable {
        public var ok: Bool
        public var threadId: String

        public init(ok: Bool, threadId: String) {
            self.ok = ok
            self.threadId = threadId
        }

        enum CodingKeys: String, CodingKey {
            case ok
            case threadId = "thread_id"
        }
    }

    public struct Outbox: Codable, Hashable, Sendable {
        public var drafts: [WaitingDraft]
        public var items: [OutboxItem]
        public var ok: Bool

        public init(drafts: [WaitingDraft], items: [OutboxItem], ok: Bool) {
            self.drafts = drafts
            self.items = items
            self.ok = ok
        }
    }

    public struct OutboxItem: Codable, Hashable, Sendable {
        public var account: String
        public var attempts: Int
        public var bccCount: Int
        public var cc: [String]
        public var createdAt: String
        public var draftId: Int?
        public var error: String?
        public var id: Int
        public var nextAttemptAt: Int
        public var retrying: Bool
        public var scheduled: Bool
        public var sentAt: String?
        public var state: String
        public var subject: String
        public var to: [String]
        public var unsure: Bool

        public init(account: String, attempts: Int, bccCount: Int, cc: [String], createdAt: String, draftId: Int? = nil, error: String? = nil, id: Int, nextAttemptAt: Int, retrying: Bool, scheduled: Bool, sentAt: String? = nil, state: String, subject: String, to: [String], unsure: Bool) {
            self.account = account
            self.attempts = attempts
            self.bccCount = bccCount
            self.cc = cc
            self.createdAt = createdAt
            self.draftId = draftId
            self.error = error
            self.id = id
            self.nextAttemptAt = nextAttemptAt
            self.retrying = retrying
            self.scheduled = scheduled
            self.sentAt = sentAt
            self.state = state
            self.subject = subject
            self.to = to
            self.unsure = unsure
        }

        enum CodingKeys: String, CodingKey {
            case account
            case attempts
            case bccCount = "bcc_count"
            case cc
            case createdAt = "created_at"
            case draftId = "draft_id"
            case error
            case id
            case nextAttemptAt = "next_attempt_at"
            case retrying
            case scheduled
            case sentAt = "sent_at"
            case state
            case subject
            case to
            case unsure
        }
    }

    /// An organizer or an attendee. An invite with no organizer holds an empty one.
    public struct Party: Codable, Hashable, Sendable {
        public var email: String?
        public var name: String?

        public init(email: String? = nil, name: String? = nil) {
            self.email = email
            self.name = name
        }
    }

    /// The Proton Bridge's password, as the Bridge shows it.
    public struct PasswordBody: Codable, Hashable, Sendable {
        public var password: String

        public init(password: String) {
            self.password = password
        }
    }

    public struct Pending: Codable, Hashable, Sendable {
        public var items: [PendingChange]
        public var stuck: Int
        public var waiting: Int

        public init(items: [PendingChange], stuck: Int, waiting: Int) {
            self.items = items
            self.stuck = stuck
            self.waiting = waiting
        }
    }

    public struct PendingChange: Codable, Hashable, Sendable {
        public var account: String
        public var attempts: Int
        public var createdAt: String
        public var dest: String?
        public var error: String?
        public var flags: [String: Bool]?
        public var folder: String
        public var id: Int
        public var nextAttemptAt: Int
        public var op: String
        public var state: String
        public var subject: String?

        public init(account: String, attempts: Int, createdAt: String, dest: String? = nil, error: String? = nil, flags: [String: Bool]? = nil, folder: String, id: Int, nextAttemptAt: Int, op: String, state: String, subject: String? = nil) {
            self.account = account
            self.attempts = attempts
            self.createdAt = createdAt
            self.dest = dest
            self.error = error
            self.flags = flags
            self.folder = folder
            self.id = id
            self.nextAttemptAt = nextAttemptAt
            self.op = op
            self.state = state
            self.subject = subject
        }

        enum CodingKeys: String, CodingKey {
            case account
            case attempts
            case createdAt = "created_at"
            case dest
            case error
            case flags
            case folder
            case id
            case nextAttemptAt = "next_attempt_at"
            case op
            case state
            case subject
        }
    }

    /// What AI cost over some days, in micro-dollars: in all, and by feature, the dearest first.
    public struct PeriodSpent: Codable, Hashable, Sendable {
        public var calls: Int
        public var costMicros: Int
        public var features: [FeatureSpent]

        public init(calls: Int, costMicros: Int, features: [FeatureSpent]) {
            self.calls = calls
            self.costMicros = costMicros
            self.features = features
        }

        enum CodingKeys: String, CodingKey {
            case calls
            case costMicros = "cost_micros"
            case features
        }
    }

    public enum PictureType: String, Codable, Hashable, Sendable, CaseIterable {
        case imagePng = "image/png"
        case imageJpeg = "image/jpeg"
        case imageGif = "image/gif"
        case imageWebp = "image/webp"
    }

    /// A route the page wrote as `/p/<id>`: the id it made, and the route as an object, which only the page reads.
    public struct Place: Codable, Hashable, Sendable {
        public var id: String
        public var route: [String: JSONValue]

        public init(id: String, route: [String: JSONValue]) {
            self.id = id
            self.route = route
        }
    }

    /// The places the page made since it last sent any. The store words what it won't keep: an id the page couldn't have made,
    /// a route too long, or more at once than it takes.
    public struct PlacesBody: Codable, Hashable, Sendable {
        public var places: [Place]

        public init(places: [Place]) {
            self.places = places
        }
    }

    public struct PlacesKept: Codable, Hashable, Sendable {
        public var ok: Bool

        public init(ok: Bool) {
            self.ok = ok
        }
    }

    public struct PlanBody: Codable, Hashable, Sendable {
        public var cmd: String?

        public init(cmd: String? = nil) {
            self.cmd = cmd
        }
    }

    public struct PlanCarriedOut: Codable, Hashable, Sendable {
        public var count: Int
        public var failed: Int
        public var gone: Int
        public var ok: Bool
        public var queued: Int
        public var undoable: Bool
        public var verb: PlanVerb

        public init(count: Int, failed: Int, gone: Int, ok: Bool, queued: Int, undoable: Bool, verb: PlanVerb) {
            self.count = count
            self.failed = failed
            self.gone = gone
            self.ok = ok
            self.queued = queued
            self.undoable = undoable
            self.verb = verb
        }
    }

    /// A command that isn't a clean, narrow action acts on nothing, and shows its filter instead.
    public struct PlanIsASearch: Codable, Hashable, Sendable {
        public var empty: Bool?
        public var filter: String
        public var intent: String
        public var ok: Bool

        public init(empty: Bool? = nil, filter: String, intent: String, ok: Bool) {
            self.empty = empty
            self.filter = filter
            self.intent = intent
            self.ok = ok
        }

        /// The keys it names, and those it requires, which a union's decoding goes by.
        static let named: Set<String> = ["empty", "filter", "intent", "ok"]
        static let required: Set<String> = ["filter", "intent", "ok"]
    }

    public struct PlanPreview: Codable, Hashable, Sendable {
        public var cap: Int
        public var capped: Bool
        public var count: Int
        public var filter: String
        public var intent: String
        public var ok: Bool
        public var plan: String
        public var sample: [PlanSample]
        public var snoozePreset: SnoozePreset
        public var verb: PlanVerb

        public init(cap: Int, capped: Bool, count: Int, filter: String, intent: String, ok: Bool, plan: String, sample: [PlanSample], snoozePreset: SnoozePreset, verb: PlanVerb) {
            self.cap = cap
            self.capped = capped
            self.count = count
            self.filter = filter
            self.intent = intent
            self.ok = ok
            self.plan = plan
            self.sample = sample
            self.snoozePreset = snoozePreset
            self.verb = verb
        }

        enum CodingKeys: String, CodingKey {
            case cap
            case capped
            case count
            case filter
            case intent
            case ok
            case plan
            case sample
            case snoozePreset = "snooze_preset"
            case verb
        }

        /// The keys it names, and those it requires, which a union's decoding goes by.
        static let named: Set<String> = ["cap", "capped", "count", "filter", "intent", "ok", "plan", "sample", "snooze_preset", "verb"]
        static let required: Set<String> = ["cap", "capped", "count", "filter", "intent", "ok", "plan", "sample", "snooze_preset", "verb"]
    }

    public struct PlanSample: Codable, Hashable, Sendable {
        public var `from`: String
        public var subject: String
        public var when: String

        public init(`from`: String, subject: String, when: String) {
            self.`from` = `from`
            self.subject = subject
            self.when = when
        }
    }

    public enum PlanVerb: String, Codable, Hashable, Sendable, CaseIterable {
        case archive
        case trash
        case junk
        case read
        case unread
        case star
        case snooze
    }

    public struct PoolCounters: Codable, Hashable, Sendable {
        public var created: Int
        public var discardDirty: Int
        public var reused: Int
        public var validateFail: Int

        public init(created: Int, discardDirty: Int, reused: Int, validateFail: Int) {
            self.created = created
            self.discardDirty = discardDirty
            self.reused = reused
            self.validateFail = validateFail
        }

        enum CodingKeys: String, CodingKey {
            case created
            case discardDirty = "discard_dirty"
            case reused
            case validateFail = "validate_fail"
        }
    }

    public struct PoolSlot: Codable, Hashable, Sendable {
        public var age: Double?
        public var `open`: Bool
        public var uses: Int

        public init(age: Double? = nil, `open`: Bool, uses: Int) {
            self.age = age
            self.`open` = `open`
            self.uses = uses
        }
    }

    /// `Pool.stats`, in counts and with no address: `target` says whether it serves every account (`all`), a list of them or
    /// those that ask for it (`listed`) or none, and `slots` holds each connection kept, with no account named.
    public struct PoolStats: Codable, Hashable, Sendable {
        public var counters: PoolCounters
        public var enabled: Bool
        public var ok: Bool
        public var slots: [PoolSlot]
        public var target: String

        public init(counters: PoolCounters, enabled: Bool, ok: Bool, slots: [PoolSlot], target: String) {
            self.counters = counters
            self.enabled = enabled
            self.ok = ok
            self.slots = slots
            self.target = target
        }
    }

    /// Every preference, as it stands: what the file says, or the default.
    public struct Preferences: Codable, Hashable, Sendable {
        public var aiBaseUrl: String
        public var aiDailyLimitUsd: Double
        public var aiEnabled: Bool
        public var aiModel: String
        public var aiProvider: AiProvider
        public var autoTidy: Bool
        public var conversations: Bool
        public var density: Density
        public var draftAhead: Bool
        public var draftTone: DraftTone
        public var guideOffered: Bool
        public var highContrast: Bool
        public var mailColors: MailColors
        public var morningBrief: Bool
        public var notifyMeetings: Bool
        public var notifyNewMail: Bool
        public var quietFrom: String
        public var quietHours: Bool
        public var quietUntil: String
        public var readFont: ReadFont
        public var readSize: ReadSize
        public var readWidth: ReadWidth
        public var searchScope: SearchScope
        public var singleKeys: Bool
        public var theme: Theme
        public var undoSendSeconds: Int
        public var weeklyReview: Bool

        public init(aiBaseUrl: String, aiDailyLimitUsd: Double, aiEnabled: Bool, aiModel: String, aiProvider: AiProvider, autoTidy: Bool, conversations: Bool, density: Density, draftAhead: Bool, draftTone: DraftTone, guideOffered: Bool, highContrast: Bool, mailColors: MailColors, morningBrief: Bool, notifyMeetings: Bool, notifyNewMail: Bool, quietFrom: String, quietHours: Bool, quietUntil: String, readFont: ReadFont, readSize: ReadSize, readWidth: ReadWidth, searchScope: SearchScope, singleKeys: Bool, theme: Theme, undoSendSeconds: Int, weeklyReview: Bool) {
            self.aiBaseUrl = aiBaseUrl
            self.aiDailyLimitUsd = aiDailyLimitUsd
            self.aiEnabled = aiEnabled
            self.aiModel = aiModel
            self.aiProvider = aiProvider
            self.autoTidy = autoTidy
            self.conversations = conversations
            self.density = density
            self.draftAhead = draftAhead
            self.draftTone = draftTone
            self.guideOffered = guideOffered
            self.highContrast = highContrast
            self.mailColors = mailColors
            self.morningBrief = morningBrief
            self.notifyMeetings = notifyMeetings
            self.notifyNewMail = notifyNewMail
            self.quietFrom = quietFrom
            self.quietHours = quietHours
            self.quietUntil = quietUntil
            self.readFont = readFont
            self.readSize = readSize
            self.readWidth = readWidth
            self.searchScope = searchScope
            self.singleKeys = singleKeys
            self.theme = theme
            self.undoSendSeconds = undoSendSeconds
            self.weeklyReview = weeklyReview
        }

        enum CodingKeys: String, CodingKey {
            case aiBaseUrl = "ai_base_url"
            case aiDailyLimitUsd = "ai_daily_limit_usd"
            case aiEnabled = "ai_enabled"
            case aiModel = "ai_model"
            case aiProvider = "ai_provider"
            case autoTidy = "auto_tidy"
            case conversations
            case density
            case draftAhead = "draft_ahead"
            case draftTone = "draft_tone"
            case guideOffered = "guide_offered"
            case highContrast = "high_contrast"
            case mailColors = "mail_colors"
            case morningBrief = "morning_brief"
            case notifyMeetings = "notify_meetings"
            case notifyNewMail = "notify_new_mail"
            case quietFrom = "quiet_from"
            case quietHours = "quiet_hours"
            case quietUntil = "quiet_until"
            case readFont = "read_font"
            case readSize = "read_size"
            case readWidth = "read_width"
            case searchScope = "search_scope"
            case singleKeys = "single_keys"
            case theme
            case undoSendSeconds = "undo_send_seconds"
            case weeklyReview = "weekly_review"
        }
    }

    public struct PreferencesAnswer: Codable, Hashable, Sendable {
        public var ok: Bool
        public var preferences: Preferences

        public init(ok: Bool, preferences: Preferences) {
            self.ok = ok
            self.preferences = preferences
        }
    }

    public struct Prefetched: Codable, Hashable, Sendable {
        public var ok: Bool
        public var prefetched: Bool

        public init(ok: Bool, prefetched: Bool) {
            self.ok = ok
            self.prefetched = prefetched
        }

        /// The keys it names, and those it requires, which a union's decoding goes by.
        static let named: Set<String> = ["ok", "prefetched"]
        static let required: Set<String> = ["ok", "prefetched"]
    }

    public enum Preset: String, Codable, Hashable, Sendable, CaseIterable {
        case gmail
        case icloud
        case yahoo
        case aol
        case fastmail
        case microsoft
        case proton
    }

    /// `Presets.preset`: a provider's preset for an address at one of its own domains, `sure`, which the form chooses; for
    /// one whose domain's mail servers are a provider's, offered (`sure` false); none for any other. `domain` is the
    /// address's, in lower case, or None when it names none a lookup could ask about. `name` is the provider's; `settings`
    /// what its account is saved as, None where MailMind can't log in to it yet (`supported` false); `note` what the form
    /// says before the password, and `link` where that password is made.
    public struct PresetFound: Codable, Hashable, Sendable {
        public var domain: String?
        public var link: Link?
        public var name: String?
        public var note: String?
        public var ok: Bool
        public var preset: Preset?
        public var settings: PresetSettings?
        public var supported: Bool
        public var sure: Bool

        public init(domain: String? = nil, link: Link? = nil, name: String? = nil, note: String? = nil, ok: Bool, preset: Preset? = nil, settings: PresetSettings? = nil, supported: Bool, sure: Bool) {
            self.domain = domain
            self.link = link
            self.name = name
            self.note = note
            self.ok = ok
            self.preset = preset
            self.settings = settings
            self.supported = supported
            self.sure = sure
        }
    }

    /// What an account of a preset is saved as, in `AccountBody`'s fields: its type, and a generic IMAP account's servers
    /// and Trash. A field its type gives is None.
    public struct PresetSettings: Codable, Hashable, Sendable {
        public var host: String?
        public var port: Int?
        public var smtpHost: String?
        public var smtpPort: Int?
        public var smtpStarttls: Bool?
        public var starttls: Bool?
        public var trash: String?
        public var type: String

        public init(host: String? = nil, port: Int? = nil, smtpHost: String? = nil, smtpPort: Int? = nil, smtpStarttls: Bool? = nil, starttls: Bool? = nil, trash: String? = nil, type: String) {
            self.host = host
            self.port = port
            self.smtpHost = smtpHost
            self.smtpPort = smtpPort
            self.smtpStarttls = smtpStarttls
            self.starttls = starttls
            self.trash = trash
            self.type = type
        }

        enum CodingKeys: String, CodingKey {
            case host
            case port
            case smtpHost = "smtp_host"
            case smtpPort = "smtp_port"
            case smtpStarttls = "smtp_starttls"
            case starttls
            case trash
            case type
        }

        /// The keys it names, and those it requires, which a union's decoding goes by.
        static let named: Set<String> = ["host", "port", "smtp_host", "smtp_port", "smtp_starttls", "starttls", "trash", "type"]
        static let required: Set<String> = ["host", "port", "smtp_host", "smtp_port", "smtp_starttls", "starttls", "trash", "type"]
    }

    public struct Preview: Codable, Hashable, Sendable {
        public var formatted: Bool
        public var html: String
        public var ok: Bool

        public init(formatted: Bool, html: String, ok: Bool) {
            self.formatted = formatted
            self.html = html
            self.ok = ok
        }
    }

    public struct PreviewBody: Codable, Hashable, Sendable {
        public var pictures: [PreviewPicture]?
        public var text: String?

        public init(pictures: [PreviewPicture]? = nil, text: String? = nil) {
            self.pictures = pictures
            self.text = text
        }
    }

    /// A file the message carries in its text. `content_type` is what it has to be to go there.
    public struct PreviewPicture: Codable, Hashable, Sendable {
        public var cid: String
        public var contentType: PictureType

        public init(cid: String, contentType: PictureType) {
            self.cid = cid
            self.contentType = contentType
        }

        enum CodingKeys: String, CodingKey {
            case cid
            case contentType = "content_type"
        }
    }

    /// An AI provider, whose key is taken out.
    public struct ProviderBody: Codable, Hashable, Sendable {
        public var provider: AiProvider

        public init(provider: AiProvider) {
            self.provider = provider
        }
    }

    public struct Queued: Codable, Hashable, Sendable {
        public var ok: Bool
        public var outboxId: Int
        public var queued: Bool
        public var recipients: Int
        public var scheduled: Bool
        public var undoSeconds: Int

        public init(ok: Bool, outboxId: Int, queued: Bool, recipients: Int, scheduled: Bool, undoSeconds: Int) {
            self.ok = ok
            self.outboxId = outboxId
            self.queued = queued
            self.recipients = recipients
            self.scheduled = scheduled
            self.undoSeconds = undoSeconds
        }

        enum CodingKeys: String, CodingKey {
            case ok
            case outboxId = "outbox_id"
            case queued
            case recipients
            case scheduled
            case undoSeconds = "undo_seconds"
        }
    }

    public struct Queues: Codable, Hashable, Sendable {
        public var commands: [String: Int]
        public var draftsWaiting: Int
        public var outbox: [String: Int]

        public init(commands: [String: Int], draftsWaiting: Int, outbox: [String: Int]) {
            self.commands = commands
            self.draftsWaiting = draftsWaiting
            self.outbox = outbox
        }

        enum CodingKeys: String, CodingKey {
            case commands
            case draftsWaiting = "drafts_waiting"
            case outbox
        }
    }

    public struct RailFolder: Codable, Hashable, Sendable {
        public var changeable: Bool
        public var name: String
        public var ownName: String
        public var role: String
        public var takesFiling: Bool
        public var total: Int
        public var unseen: Int

        public init(changeable: Bool, name: String, ownName: String, role: String, takesFiling: Bool, total: Int, unseen: Int) {
            self.changeable = changeable
            self.name = name
            self.ownName = ownName
            self.role = role
            self.takesFiling = takesFiling
            self.total = total
            self.unseen = unseen
        }

        enum CodingKeys: String, CodingKey {
            case changeable
            case name
            case ownName = "own_name"
            case role
            case takesFiling = "takes_filing"
            case total
            case unseen
        }
    }

    public struct RationaleBody: Codable, Hashable, Sendable {
        public var senders: [String]?

        public init(senders: [String]? = nil) {
            self.senders = senders
        }
    }

    public enum ReadFont: String, Codable, Hashable, Sendable, CaseIterable {
        case sans
        case serif
        case mono
    }

    public enum ReadSize: String, Codable, Hashable, Sendable, CaseIterable {
        case s
        case m
        case l
        case xl
    }

    public enum ReadWidth: String, Codable, Hashable, Sendable, CaseIterable {
        case narrow
        case medium
        case wide
    }

    public struct RecentMail: Codable, Hashable, Sendable {
        public var account: String
        public var folder: String
        public var key: Int
        public var seen: Bool
        public var subject: String
        public var threadId: String?
        public var uid: Int
        public var when: String

        public init(account: String, folder: String, key: Int, seen: Bool, subject: String, threadId: String? = nil, uid: Int, when: String) {
            self.account = account
            self.folder = folder
            self.key = key
            self.seen = seen
            self.subject = subject
            self.threadId = threadId
            self.uid = uid
            self.when = when
        }

        enum CodingKeys: String, CodingKey {
            case account
            case folder
            case key
            case seen
            case subject
            case threadId = "thread_id"
            case uid
            case when
        }
    }

    public struct ReconcileBody: Codable, Hashable, Sendable {
        public var account: String?
        public var dryRun: Bool?

        public init(account: String? = nil, dryRun: Bool? = nil) {
            self.account = account
            self.dryRun = dryRun
        }

        enum CodingKeys: String, CodingKey {
            case account
            case dryRun = "dry_run"
        }
    }

    public struct Reconciled: Codable, Hashable, Sendable {
        public var dryRun: Bool
        public var generated: String
        public var ok: Bool
        public var results: [AccountReconciled]
        public var totalGhosts: Int
        public var totalPruned: Int

        public init(dryRun: Bool, generated: String, ok: Bool, results: [AccountReconciled], totalGhosts: Int, totalPruned: Int) {
            self.dryRun = dryRun
            self.generated = generated
            self.ok = ok
            self.results = results
            self.totalGhosts = totalGhosts
            self.totalPruned = totalPruned
        }

        enum CodingKeys: String, CodingKey {
            case dryRun = "dry_run"
            case generated
            case ok
            case results
            case totalGhosts = "total_ghosts"
            case totalPruned = "total_pruned"
        }
    }

    /// The account to remove, and whether this Mac lets go of what it keeps of its mail too.
    public struct RemoveBody: Codable, Hashable, Sendable {
        public var deleteMail: Bool?
        public var email: String?

        public init(deleteMail: Bool? = nil, email: String? = nil) {
            self.deleteMail = deleteMail
            self.email = email
        }

        enum CodingKeys: String, CodingKey {
            case deleteMail = "delete_mail"
            case email
        }
    }

    /// `mail_deleted`, with `delete_mail` asked: how many stored copies of its mail this Mac let go.
    public struct Removed: Codable, Hashable, Sendable {
        public var mailDeleted: Int?
        public var ok: Bool
        public var removed: String

        public init(mailDeleted: Int? = nil, ok: Bool, removed: String) {
            self.mailDeleted = mailDeleted
            self.ok = ok
            self.removed = removed
        }

        enum CodingKeys: String, CodingKey {
            case mailDeleted = "mail_deleted"
            case ok
            case removed
        }
    }

    public struct RenameFolderBody: Codable, Hashable, Sendable {
        public var account: String?
        public var folder: String?
        public var name: String?

        public init(account: String? = nil, folder: String? = nil, name: String? = nil) {
            self.account = account
            self.folder = folder
            self.name = name
        }
    }

    public struct RepliedLater: Codable, Hashable, Sendable {
        public var ok: Bool
        public var on: Bool
        public var threadId: String

        public init(ok: Bool, on: Bool, threadId: String) {
            self.ok = ok
            self.on = on
            self.threadId = threadId
        }

        enum CodingKeys: String, CodingKey {
            case ok
            case on
            case threadId = "thread_id"
        }
    }

    /// A reply already saved to a message, which Reply opens rather than a second, empty one: its newest.
    public struct ReplyDraft: Codable, Hashable, Sendable {
        public var draftId: Int?
        public var inDrafts: Copy?
        public var more: Int

        public init(draftId: Int? = nil, inDrafts: Copy? = nil, more: Int) {
            self.draftId = draftId
            self.inDrafts = inDrafts
            self.more = more
        }

        enum CodingKeys: String, CodingKey {
            case draftId = "draft_id"
            case inDrafts = "in_drafts"
            case more
        }

        /// The keys it names, and those it requires, which a union's decoding goes by.
        static let named: Set<String> = ["draft_id", "in_drafts", "more"]
        static let required: Set<String> = ["draft_id", "in_drafts", "more"]
    }

    public struct ReplyLaterBody: Codable, Hashable, Sendable {
        public var account: String?
        public var folder: String?
        public var key: Int?
        public var on: Bool?
        public var threadId: String?
        public var uid: Int?

        public init(account: String? = nil, folder: String? = nil, key: Int? = nil, on: Bool? = nil, threadId: String? = nil, uid: Int? = nil) {
            self.account = account
            self.folder = folder
            self.key = key
            self.on = on
            self.threadId = threadId
            self.uid = uid
        }

        enum CodingKeys: String, CodingKey {
            case account
            case folder
            case key
            case on
            case threadId = "thread_id"
            case uid
        }
    }

    public struct ReplyLaterListing: Codable, Hashable, Sendable {
        public var account: String?
        public var folder: String
        public var hasMore: Bool
        public var items: [ReplyLaterRow]
        public var offset: Int
        public var ok: Bool
        public var replylater: Bool
        public var total: Int

        public init(account: String? = nil, folder: String, hasMore: Bool, items: [ReplyLaterRow], offset: Int, ok: Bool, replylater: Bool, total: Int) {
            self.account = account
            self.folder = folder
            self.hasMore = hasMore
            self.items = items
            self.offset = offset
            self.ok = ok
            self.replylater = replylater
            self.total = total
        }

        enum CodingKeys: String, CodingKey {
            case account
            case folder
            case hasMore = "has_more"
            case items
            case offset
            case ok
            case replylater
            case total
        }

        /// The keys it names, and those it requires, which a union's decoding goes by.
        static let named: Set<String> = ["account", "folder", "has_more", "items", "offset", "ok", "replylater", "total"]
        static let required: Set<String> = ["account", "folder", "has_more", "items", "offset", "ok", "replylater", "total"]
    }

    public struct ReplyLaterRow: Codable, Hashable, Sendable {
        public var account: String
        public var bucket: String?
        public var count: Int
        public var flagged: Bool
        public var folder: String
        public var fromAddr: String?
        public var fromName: String?
        public var hasAttach: Bool
        public var isJunk: Bool
        public var key: Int
        public var replylater: Bool
        public var seen: Bool
        public var sentTo: [Addressee]
        public var sentToMore: Int
        public var snippet: String
        public var subject: String
        public var threadId: String?
        public var ts: Int?
        public var uid: Int
        public var waitingLabel: String
        public var when: String

        public init(account: String, bucket: String? = nil, count: Int, flagged: Bool, folder: String, fromAddr: String? = nil, fromName: String? = nil, hasAttach: Bool, isJunk: Bool, key: Int, replylater: Bool, seen: Bool, sentTo: [Addressee], sentToMore: Int, snippet: String, subject: String, threadId: String? = nil, ts: Int? = nil, uid: Int, waitingLabel: String, when: String) {
            self.account = account
            self.bucket = bucket
            self.count = count
            self.flagged = flagged
            self.folder = folder
            self.fromAddr = fromAddr
            self.fromName = fromName
            self.hasAttach = hasAttach
            self.isJunk = isJunk
            self.key = key
            self.replylater = replylater
            self.seen = seen
            self.sentTo = sentTo
            self.sentToMore = sentToMore
            self.snippet = snippet
            self.subject = subject
            self.threadId = threadId
            self.ts = ts
            self.uid = uid
            self.waitingLabel = waitingLabel
            self.when = when
        }

        enum CodingKeys: String, CodingKey {
            case account
            case bucket
            case count
            case flagged
            case folder
            case fromAddr = "from_addr"
            case fromName = "from_name"
            case hasAttach = "has_attach"
            case isJunk = "is_junk"
            case key
            case replylater
            case seen
            case sentTo = "sent_to"
            case sentToMore = "sent_to_more"
            case snippet
            case subject
            case threadId = "thread_id"
            case ts
            case uid
            case waitingLabel = "waiting_label"
            case when
        }
    }

    /// A draft MailMind saved, by its id, or a copy of one in Drafts by its key and folder.
    public struct ResumeBody: Codable, Hashable, Sendable {
        public var draftId: Int?
        public var folder: String?
        public var key: Int?
        public var uid: Int?

        public init(draftId: Int? = nil, folder: String? = nil, key: Int? = nil, uid: Int? = nil) {
            self.draftId = draftId
            self.folder = folder
            self.key = key
            self.uid = uid
        }

        enum CodingKeys: String, CodingKey {
            case draftId = "draft_id"
            case folder
            case key
            case uid
        }
    }

    public struct Retried: Codable, Hashable, Sendable {
        public var gone: Bool?
        public var ok: Bool

        public init(gone: Bool? = nil, ok: Bool) {
            self.gone = gone
            self.ok = ok
        }
    }

    public struct Reversible: Codable, Hashable, Sendable {
        public var block: Bool
        public var sweep: Bool
        public var unsubscribe: Bool

        public init(block: Bool, sweep: Bool, unsubscribe: Bool) {
            self.block = block
            self.sweep = sweep
            self.unsubscribe = unsubscribe
        }
    }

    public struct Review: Codable, Hashable, Sendable {
        public var cached: Bool
        public var generated: String
        public var meetings: [ReviewMeeting]
        public var model: String
        public var narrative: String
        public var needs: [ReviewNeeded]
        public var next: [String]
        public var ok: Bool
        public var stale: Bool
        public var stats: ReviewStats
        public var waiting: [ReviewAwaited]
        public var weekStart: String

        public init(cached: Bool, generated: String, meetings: [ReviewMeeting], model: String, narrative: String, needs: [ReviewNeeded], next: [String], ok: Bool, stale: Bool, stats: ReviewStats, waiting: [ReviewAwaited], weekStart: String) {
            self.cached = cached
            self.generated = generated
            self.meetings = meetings
            self.model = model
            self.narrative = narrative
            self.needs = needs
            self.next = next
            self.ok = ok
            self.stale = stale
            self.stats = stats
            self.waiting = waiting
            self.weekStart = weekStart
        }

        enum CodingKeys: String, CodingKey {
            case cached
            case generated
            case meetings
            case model
            case narrative
            case needs
            case next
            case ok
            case stale
            case stats
            case waiting
            case weekStart = "week_start"
        }

        /// The keys it names, and those it requires, which a union's decoding goes by.
        static let named: Set<String> = ["cached", "generated", "meetings", "model", "narrative", "needs", "next", "ok", "stale", "stats", "waiting", "week_start"]
        static let required: Set<String> = ["cached", "generated", "meetings", "model", "narrative", "needs", "next", "ok", "stale", "stats", "waiting", "week_start"]
    }

    /// A thread you're waiting on. One whose thread has no row names no message.
    public struct ReviewAwaited: Codable, Hashable, Sendable {
        public var account: String
        public var folder: String?
        public var key: Int?
        public var sentTs: Int
        public var subject: String
        public var threadId: String
        public var to: String
        public var uid: Int?

        public init(account: String, folder: String? = nil, key: Int? = nil, sentTs: Int, subject: String, threadId: String, to: String, uid: Int? = nil) {
            self.account = account
            self.folder = folder
            self.key = key
            self.sentTs = sentTs
            self.subject = subject
            self.threadId = threadId
            self.to = to
            self.uid = uid
        }

        enum CodingKeys: String, CodingKey {
            case account
            case folder
            case key
            case sentTs = "sent_ts"
            case subject
            case threadId = "thread_id"
            case to
            case uid
        }
    }

    /// An event in the week's invites, by its message's key and the folder and uid of its copy now.
    public struct ReviewMeeting: Codable, Hashable, Sendable {
        public var account: String
        public var allDay: Bool
        public var folder: String
        public var key: Int?
        public var past: Bool
        public var start: String
        public var summary: String
        public var threadId: String?
        public var uid: Int?

        public init(account: String, allDay: Bool, folder: String, key: Int? = nil, past: Bool, start: String, summary: String, threadId: String? = nil, uid: Int? = nil) {
            self.account = account
            self.allDay = allDay
            self.folder = folder
            self.key = key
            self.past = past
            self.start = start
            self.summary = summary
            self.threadId = threadId
            self.uid = uid
        }

        enum CodingKeys: String, CodingKey {
            case account
            case allDay = "all_day"
            case folder
            case key
            case past
            case start
            case summary
            case threadId = "thread_id"
            case uid
        }
    }

    /// None is stored yet, and a read writes none.
    public struct ReviewMissing: Codable, Hashable, Sendable {
        public var missing: Bool
        public var ok: Bool

        public init(missing: Bool, ok: Bool) {
            self.missing = missing
            self.ok = ok
        }

        /// The keys it names, and those it requires, which a union's decoding goes by.
        static let named: Set<String> = ["missing", "ok"]
        static let required: Set<String> = ["missing", "ok"]
    }

    /// A message that still needs you, by its key and the folder and uid of its copy now.
    public struct ReviewNeeded: Codable, Hashable, Sendable {
        public var account: String
        public var action: String
        public var folder: String
        public var importance: Int
        public var key: Int?
        public var subject: String
        public var threadId: String?
        public var uid: Int?
        public var who: String

        public init(account: String, action: String, folder: String, importance: Int, key: Int? = nil, subject: String, threadId: String? = nil, uid: Int? = nil, who: String) {
            self.account = account
            self.action = action
            self.folder = folder
            self.importance = importance
            self.key = key
            self.subject = subject
            self.threadId = threadId
            self.uid = uid
            self.who = who
        }

        enum CodingKeys: String, CodingKey {
            case account
            case action
            case folder
            case importance
            case key
            case subject
            case threadId = "thread_id"
            case uid
            case who
        }
    }

    public struct ReviewStats: Codable, Hashable, Sendable {
        public var byBucket: [String: Int]
        public var cleaned: Int
        public var handled: Int
        public var needsYou: Int
        public var received: Int
        public var replyLater: Int
        public var sent: Int
        public var waitingOn: Int

        public init(byBucket: [String: Int], cleaned: Int, handled: Int, needsYou: Int, received: Int, replyLater: Int, sent: Int, waitingOn: Int) {
            self.byBucket = byBucket
            self.cleaned = cleaned
            self.handled = handled
            self.needsYou = needsYou
            self.received = received
            self.replyLater = replyLater
            self.sent = sent
            self.waitingOn = waitingOn
        }

        enum CodingKeys: String, CodingKey {
            case byBucket = "by_bucket"
            case cleaned
            case handled
            case needsYou = "needs_you"
            case received
            case replyLater = "reply_later"
            case sent
            case waitingOn = "waiting_on"
        }
    }

    public struct RewriteBody: Codable, Hashable, Sendable {
        public var lang: String?
        public var op: RewriteOp?
        public var text: String?

        public init(lang: String? = nil, op: RewriteOp? = nil, text: String? = nil) {
            self.lang = lang
            self.op = op
            self.text = text
        }
    }

    public enum RewriteOp: String, Codable, Hashable, Sendable, CaseIterable {
        case shorten
        case elaborate
        case formal
        case friendly
        case fix
        case translate
    }

    public struct Rewritten: Codable, Hashable, Sendable {
        public var ok: Bool
        public var op: RewriteOp
        public var text: String

        public init(ok: Bool, op: RewriteOp, text: String) {
            self.ok = ok
            self.op = op
            self.text = text
        }
    }

    public struct RowsFiled: Codable, Hashable, Sendable {
        public var count: Int
        public var failed: Int
        public var filed: [Copy]
        public var msg: String
        public var ok: Bool
        public var partial: Bool
        public var queued: Int
        public var rowsFailed: Int
        public var undoable: Bool

        public init(count: Int, failed: Int, filed: [Copy], msg: String, ok: Bool, partial: Bool, queued: Int, rowsFailed: Int, undoable: Bool) {
            self.count = count
            self.failed = failed
            self.filed = filed
            self.msg = msg
            self.ok = ok
            self.partial = partial
            self.queued = queued
            self.rowsFailed = rowsFailed
            self.undoable = undoable
        }

        enum CodingKeys: String, CodingKey {
            case count
            case failed
            case filed
            case msg
            case ok
            case partial
            case queued
            case rowsFailed = "rows_failed"
            case undoable
        }
    }

    /// The invite, by its message's key, and the folder and uid of the copy the page has (`mailmind.http.named`).
    public struct RsvpBody: Codable, Hashable, Sendable {
        public var decision: RsvpDecision?
        public var folder: String?
        public var key: Int
        public var uid: Int?

        public init(decision: RsvpDecision? = nil, folder: String? = nil, key: Int, uid: Int? = nil) {
            self.decision = decision
            self.folder = folder
            self.key = key
            self.uid = uid
        }
    }

    public enum RsvpDecision: String, Codable, Hashable, Sendable, CaseIterable {
        case accept
        case decline
        case tentative
    }

    public struct RsvpQueued: Codable, Hashable, Sendable {
        public var decision: RsvpDecision
        public var ok: Bool
        public var organizer: String
        public var outboxId: Int
        public var queued: Bool
        public var summary: String
        public var undoSeconds: Int

        public init(decision: RsvpDecision, ok: Bool, organizer: String, outboxId: Int, queued: Bool, summary: String, undoSeconds: Int) {
            self.decision = decision
            self.ok = ok
            self.organizer = organizer
            self.outboxId = outboxId
            self.queued = queued
            self.summary = summary
            self.undoSeconds = undoSeconds
        }

        enum CodingKeys: String, CodingKey {
            case decision
            case ok
            case organizer
            case outboxId = "outbox_id"
            case queued
            case summary
            case undoSeconds = "undo_seconds"
        }
    }

    /// A block trashes the copy `key` and `folder` name too, if it is still there.
    public struct RuleBody: Codable, Hashable, Sendable {
        public var folder: String?
        public var key: Int?
        public var token: String?
        public var uid: Int?
        public var verdict: RuleChange?

        public init(folder: String? = nil, key: Int? = nil, token: String? = nil, uid: Int? = nil, verdict: RuleChange? = nil) {
            self.folder = folder
            self.key = key
            self.token = token
            self.uid = uid
            self.verdict = verdict
        }
    }

    public struct RuleBook: Codable, Hashable, Sendable {
        public var rules: Rules

        public init(rules: Rules) {
            self.rules = rules
        }
    }

    public enum RuleChange: String, Codable, Hashable, Sendable, CaseIterable {
        case keep
        case block
        case remove
    }

    public struct RuleChanged: Codable, Hashable, Sendable {
        public var ok: Bool
        public var placedIn: String?
        public var reclassified: Int
        public var rules: Rules
        public var token: String
        public var trashed: Bool

        public init(ok: Bool, placedIn: String? = nil, reclassified: Int, rules: Rules, token: String, trashed: Bool) {
            self.ok = ok
            self.placedIn = placedIn
            self.reclassified = reclassified
            self.rules = rules
            self.token = token
            self.trashed = trashed
        }

        enum CodingKeys: String, CodingKey {
            case ok
            case placedIn = "placed_in"
            case reclassified
            case rules
            case token
            case trashed
        }
    }

    public struct Rules: Codable, Hashable, Sendable {
        public var blockDomains: [String]
        public var blockSenders: [String]
        public var expireHours: [String: Int]
        public var filters: [MailFilter]
        public var keepDomains: [String]
        public var keepSenders: [String]

        public init(blockDomains: [String], blockSenders: [String], expireHours: [String: Int], filters: [MailFilter], keepDomains: [String], keepSenders: [String]) {
            self.blockDomains = blockDomains
            self.blockSenders = blockSenders
            self.expireHours = expireHours
            self.filters = filters
            self.keepDomains = keepDomains
            self.keepSenders = keepSenders
        }

        enum CodingKeys: String, CodingKey {
            case blockDomains = "block_domains"
            case blockSenders = "block_senders"
            case expireHours = "expire_hours"
            case filters
            case keepDomains = "keep_domains"
            case keepSenders = "keep_senders"
        }
    }

    public struct Saved: Codable, Hashable, Sendable {
        public var account: AccountRow
        public var ok: Bool

        public init(account: AccountRow, ok: Bool) {
            self.account = account
            self.ok = ok
        }
    }

    public struct ScreenerItem: Codable, Hashable, Sendable {
        public var count: Int
        public var domain: String
        public var fromAddr: String
        public var fromName: String
        public var when: String

        public init(count: Int, domain: String, fromAddr: String, fromName: String, when: String) {
            self.count = count
            self.domain = domain
            self.fromAddr = fromAddr
            self.fromName = fromName
            self.when = when
        }

        enum CodingKeys: String, CodingKey {
            case count
            case domain
            case fromAddr = "from_addr"
            case fromName = "from_name"
            case when
        }
    }

    public struct ScreenerQueue: Codable, Hashable, Sendable {
        public var account: String?
        public var items: [ScreenerItem]
        public var ok: Bool
        public var total: Int

        public init(account: String? = nil, items: [ScreenerItem], ok: Bool, total: Int) {
            self.account = account
            self.items = items
            self.ok = ok
            self.total = total
        }
    }

    public struct ScreenerSuggestBody: Codable, Hashable, Sendable {
        public var account: String?

        public init(account: String? = nil) {
            self.account = account
        }
    }

    /// What the AI would do about a sender, surely enough to say so. A person still clicks: nothing writes a rule from this.
    public struct ScreenerSuggestion: Codable, Hashable, Sendable {
        public var confidence: Double
        public var fromAddr: String
        public var verdict: Verdict
        public var why: String

        public init(confidence: Double, fromAddr: String, verdict: Verdict, why: String) {
            self.confidence = confidence
            self.fromAddr = fromAddr
            self.verdict = verdict
            self.why = why
        }

        enum CodingKeys: String, CodingKey {
            case confidence
            case fromAddr = "from_addr"
            case verdict
            case why
        }
    }

    public struct ScreenerSuggestions: Codable, Hashable, Sendable {
        public var account: String?
        public var asked: Int
        public var ok: Bool
        public var suggestions: [ScreenerSuggestion]

        public init(account: String? = nil, asked: Int, ok: Bool, suggestions: [ScreenerSuggestion]) {
            self.account = account
            self.asked = asked
            self.ok = ok
            self.suggestions = suggestions
        }
    }

    public struct SearchHit: Codable, Hashable, Sendable {
        public var account: String
        public var action: String?
        public var alias: String?
        public var bucket: String?
        public var commitmentWorthy: Bool
        public var date: String
        public var flagged: Bool
        public var folder: String
        public var fromAddr: String?
        public var fromName: String?
        public var hasAttach: Bool
        public var importance: Int?
        public var isJunk: Bool
        public var key: Int
        public var needsYou: Bool
        public var replyWorthy: Bool
        public var safeToFile: Bool
        public var seen: Bool
        public var sentTo: [Addressee]
        public var sentToMore: Int
        public var snippet: String
        public var subject: String
        public var threadId: String?
        public var uid: Int
        public var when: String

        public init(account: String, action: String? = nil, alias: String? = nil, bucket: String? = nil, commitmentWorthy: Bool, date: String, flagged: Bool, folder: String, fromAddr: String? = nil, fromName: String? = nil, hasAttach: Bool, importance: Int? = nil, isJunk: Bool, key: Int, needsYou: Bool, replyWorthy: Bool, safeToFile: Bool, seen: Bool, sentTo: [Addressee], sentToMore: Int, snippet: String, subject: String, threadId: String? = nil, uid: Int, when: String) {
            self.account = account
            self.action = action
            self.alias = alias
            self.bucket = bucket
            self.commitmentWorthy = commitmentWorthy
            self.date = date
            self.flagged = flagged
            self.folder = folder
            self.fromAddr = fromAddr
            self.fromName = fromName
            self.hasAttach = hasAttach
            self.importance = importance
            self.isJunk = isJunk
            self.key = key
            self.needsYou = needsYou
            self.replyWorthy = replyWorthy
            self.safeToFile = safeToFile
            self.seen = seen
            self.sentTo = sentTo
            self.sentToMore = sentToMore
            self.snippet = snippet
            self.subject = subject
            self.threadId = threadId
            self.uid = uid
            self.when = when
        }

        enum CodingKeys: String, CodingKey {
            case account
            case action
            case alias
            case bucket
            case commitmentWorthy = "commitment_worthy"
            case date
            case flagged
            case folder
            case fromAddr = "from_addr"
            case fromName = "from_name"
            case hasAttach = "has_attach"
            case importance
            case isJunk = "is_junk"
            case key
            case needsYou = "needs_you"
            case replyWorthy = "reply_worthy"
            case safeToFile = "safe_to_file"
            case seen
            case sentTo = "sent_to"
            case sentToMore = "sent_to_more"
            case snippet
            case subject
            case threadId = "thread_id"
            case uid
            case when
        }
    }

    public struct SearchPage: Codable, Hashable, Sendable {
        public var generated: String
        public var hasMore: Bool
        public var items: [SearchHit]
        public var offset: Int
        public var ok: Bool
        public var query: String
        public var scope: String

        public init(generated: String, hasMore: Bool, items: [SearchHit], offset: Int, ok: Bool, query: String, scope: String) {
            self.generated = generated
            self.hasMore = hasMore
            self.items = items
            self.offset = offset
            self.ok = ok
            self.query = query
            self.scope = scope
        }

        enum CodingKeys: String, CodingKey {
            case generated
            case hasMore = "has_more"
            case items
            case offset
            case ok
            case query
            case scope
        }

        /// The keys it names, and those it requires, which a union's decoding goes by.
        static let named: Set<String> = ["generated", "has_more", "items", "offset", "ok", "query", "scope"]
        static let required: Set<String> = ["generated", "has_more", "items", "offset", "ok", "query", "scope"]
    }

    public enum SearchScope: String, Codable, Hashable, Sendable, CaseIterable {
        case all
        case account
    }

    public struct SearchSuggestion: Codable, Hashable, Sendable {
        public var complete: Bool
        public var detail: String
        public var label: String
        public var value: String

        public init(complete: Bool, detail: String, label: String, value: String) {
            self.complete = complete
            self.detail = detail
            self.label = label
            self.value = value
        }
    }

    public struct SearchSuggestions: Codable, Hashable, Sendable {
        public var items: [SearchSuggestion]
        public var ok: Bool

        public init(items: [SearchSuggestion], ok: Bool) {
            self.items = items
            self.ok = ok
        }
    }

    public struct SeenBody: Codable, Hashable, Sendable {
        public var folder: String
        public var key: Int
        public var mutationId: String?
        public var seen: Bool?
        public var uid: Int?

        public init(folder: String, key: Int, mutationId: String? = nil, seen: Bool? = nil, uid: Int? = nil) {
            self.folder = folder
            self.key = key
            self.mutationId = mutationId
            self.seen = seen
            self.uid = uid
        }

        enum CodingKeys: String, CodingKey {
            case folder
            case key
            case mutationId = "mutation_id"
            case seen
            case uid
        }
    }

    public struct SendBody: Codable, Hashable, Sendable {
        public var account: String?
        public var attachments: [JSONValue]?
        public var bcc: SendBodyBcc?
        public var cc: SendBodyCc?
        public var draftId: Int?
        public var forwardAttachments: Bool?
        public var idempotencyKey: String?
        public var mode: ComposeMode?
        public var originalKey: Int?
        public var resume: [String: JSONValue]?
        public var sendAfter: Int?
        public var subject: String?
        public var text: String?
        public var to: SendBodyTo?

        public init(account: String? = nil, attachments: [JSONValue]? = nil, bcc: SendBodyBcc? = nil, cc: SendBodyCc? = nil, draftId: Int? = nil, forwardAttachments: Bool? = nil, idempotencyKey: String? = nil, mode: ComposeMode? = nil, originalKey: Int? = nil, resume: [String: JSONValue]? = nil, sendAfter: Int? = nil, subject: String? = nil, text: String? = nil, to: SendBodyTo? = nil) {
            self.account = account
            self.attachments = attachments
            self.bcc = bcc
            self.cc = cc
            self.draftId = draftId
            self.forwardAttachments = forwardAttachments
            self.idempotencyKey = idempotencyKey
            self.mode = mode
            self.originalKey = originalKey
            self.resume = resume
            self.sendAfter = sendAfter
            self.subject = subject
            self.text = text
            self.to = to
        }

        enum CodingKeys: String, CodingKey {
            case account
            case attachments
            case bcc
            case cc
            case draftId = "draft_id"
            case forwardAttachments = "forward_attachments"
            case idempotencyKey = "idempotency_key"
            case mode
            case originalKey = "original_key"
            case resume
            case sendAfter = "send_after"
            case subject
            case text
            case to
        }
    }

    public struct SenderAccount: Codable, Hashable, Sendable {
        public var count: Int
        public var email: String

        public init(count: Int, email: String) {
            self.count = count
            self.email = email
        }
    }

    /// What the reader says beside a sender's name: what their card opens with.
    public struct SenderFacts: Codable, Hashable, Sendable {
        public var count: Int
        public var firstAgo: String?
        public var replied: Int

        public init(count: Int, firstAgo: String? = nil, replied: Int) {
            self.count = count
            self.firstAgo = firstAgo
            self.replied = replied
        }

        enum CodingKeys: String, CodingKey {
            case count
            case firstAgo = "first_ago"
            case replied
        }

        /// The keys it names, and those it requires, which a union's decoding goes by.
        static let named: Set<String> = ["count", "first_ago", "replied"]
        static let required: Set<String> = ["count", "first_ago", "replied"]
    }

    public struct SenderPreview: Codable, Hashable, Sendable {
        public var accounts: [String]
        public var confirmToken: String
        public var count: Int
        public var domain: String
        public var fromAddr: String
        public var ok: Bool
        public var reversible: Reversible
        public var sweepCapped: Bool
        public var sweepCount: Int
        public var unseen: Int
        public var unsub: WayOut

        public init(accounts: [String], confirmToken: String, count: Int, domain: String, fromAddr: String, ok: Bool, reversible: Reversible, sweepCapped: Bool, sweepCount: Int, unseen: Int, unsub: WayOut) {
            self.accounts = accounts
            self.confirmToken = confirmToken
            self.count = count
            self.domain = domain
            self.fromAddr = fromAddr
            self.ok = ok
            self.reversible = reversible
            self.sweepCapped = sweepCapped
            self.sweepCount = sweepCount
            self.unseen = unseen
            self.unsub = unsub
        }

        enum CodingKeys: String, CodingKey {
            case accounts
            case confirmToken = "confirm_token"
            case count
            case domain
            case fromAddr = "from_addr"
            case ok
            case reversible
            case sweepCapped = "sweep_capped"
            case sweepCount = "sweep_count"
            case unseen
            case unsub
        }
    }

    public struct SenderSummary: Codable, Hashable, Sendable {
        public var addr: String
        public var ok: Bool
        public var `open`: String
        public var summary: String
        public var threads: Int
        public var topics: [String]

        public init(addr: String, ok: Bool, `open`: String, summary: String, threads: Int, topics: [String]) {
            self.addr = addr
            self.ok = ok
            self.`open` = `open`
            self.summary = summary
            self.threads = threads
            self.topics = topics
        }
    }

    public struct SenderSummaryBody: Codable, Hashable, Sendable {
        public var addr: String?

        public init(addr: String? = nil) {
            self.addr = addr
        }
    }

    public struct SentNow: Codable, Hashable, Sendable {
        public var ok: Bool

        public init(ok: Bool) {
            self.ok = ok
        }
    }

    public struct Snippet: Codable, Hashable, Sendable {
        public var text: String
        public var trigger: String

        public init(text: String, trigger: String) {
            self.text = text
            self.trigger = trigger
        }
    }

    public struct SnippetChange: Codable, Hashable, Sendable {
        public var text: String?
        public var trigger: String?
        public var verb: SnippetVerb?

        public init(text: String? = nil, trigger: String? = nil, verb: SnippetVerb? = nil) {
            self.text = text
            self.trigger = trigger
            self.verb = verb
        }
    }

    public enum SnippetVerb: String, Codable, Hashable, Sendable, CaseIterable {
        case save
        case delete
    }

    public struct Snippets: Codable, Hashable, Sendable {
        public var ok: Bool
        public var snippets: [Snippet]

        public init(ok: Bool, snippets: [Snippet]) {
            self.ok = ok
            self.snippets = snippets
        }
    }

    public struct SnoozeBody: Codable, Hashable, Sendable {
        public var account: String?
        public var copies: [ACopy]?
        public var folder: String?
        public var key: Int?
        public var preset: SnoozePreset?
        public var threadId: String?
        public var uid: Int?
        public var undoGroup: String?
        public var until: SnoozeBodyUntil?

        public init(account: String? = nil, copies: [ACopy]? = nil, folder: String? = nil, key: Int? = nil, preset: SnoozePreset? = nil, threadId: String? = nil, uid: Int? = nil, undoGroup: String? = nil, until: SnoozeBodyUntil? = nil) {
            self.account = account
            self.copies = copies
            self.folder = folder
            self.key = key
            self.preset = preset
            self.threadId = threadId
            self.uid = uid
            self.undoGroup = undoGroup
            self.until = until
        }

        enum CodingKeys: String, CodingKey {
            case account
            case copies
            case folder
            case key
            case preset
            case threadId = "thread_id"
            case uid
            case undoGroup = "undo_group"
            case until
        }
    }

    public enum SnoozePreset: String, Codable, Hashable, Sendable, CaseIterable {
        case laterToday = "later_today"
        case tomorrow
        case weekend
        case nextWeek = "next_week"
    }

    public struct Snoozed: Codable, Hashable, Sendable {
        public var count: Int
        public var ok: Bool
        public var until: Int

        public init(count: Int, ok: Bool, until: Int) {
            self.count = count
            self.ok = ok
            self.until = until
        }
    }

    public struct SnoozedListing: Codable, Hashable, Sendable {
        public var account: String?
        public var folder: String
        public var hasMore: Bool
        public var items: [SnoozedRow]
        public var offset: Int
        public var ok: Bool
        public var snoozed: Bool
        public var total: Int

        public init(account: String? = nil, folder: String, hasMore: Bool, items: [SnoozedRow], offset: Int, ok: Bool, snoozed: Bool, total: Int) {
            self.account = account
            self.folder = folder
            self.hasMore = hasMore
            self.items = items
            self.offset = offset
            self.ok = ok
            self.snoozed = snoozed
            self.total = total
        }

        enum CodingKeys: String, CodingKey {
            case account
            case folder
            case hasMore = "has_more"
            case items
            case offset
            case ok
            case snoozed
            case total
        }

        /// The keys it names, and those it requires, which a union's decoding goes by.
        static let named: Set<String> = ["account", "folder", "has_more", "items", "offset", "ok", "snoozed", "total"]
        static let required: Set<String> = ["account", "folder", "has_more", "items", "offset", "ok", "snoozed", "total"]
    }

    public struct SnoozedRow: Codable, Hashable, Sendable {
        public var account: String
        public var bucket: String?
        public var commitmentWorthy: Bool
        public var flagged: Bool
        public var folder: String
        public var fromAddr: String?
        public var fromName: String?
        public var hasAttach: Bool
        public var importance: Int?
        public var isJunk: Bool
        public var key: Int
        public var needsYou: Bool
        public var replyWorthy: Bool
        public var safeToFile: Bool
        public var seen: Bool
        public var sentTo: [Addressee]
        public var sentToMore: Int
        public var snippet: String
        public var snoozedUntil: Int
        public var subject: String
        public var threadId: String?
        public var uid: Int
        public var when: String

        public init(account: String, bucket: String? = nil, commitmentWorthy: Bool, flagged: Bool, folder: String, fromAddr: String? = nil, fromName: String? = nil, hasAttach: Bool, importance: Int? = nil, isJunk: Bool, key: Int, needsYou: Bool, replyWorthy: Bool, safeToFile: Bool, seen: Bool, sentTo: [Addressee], sentToMore: Int, snippet: String, snoozedUntil: Int, subject: String, threadId: String? = nil, uid: Int, when: String) {
            self.account = account
            self.bucket = bucket
            self.commitmentWorthy = commitmentWorthy
            self.flagged = flagged
            self.folder = folder
            self.fromAddr = fromAddr
            self.fromName = fromName
            self.hasAttach = hasAttach
            self.importance = importance
            self.isJunk = isJunk
            self.key = key
            self.needsYou = needsYou
            self.replyWorthy = replyWorthy
            self.safeToFile = safeToFile
            self.seen = seen
            self.sentTo = sentTo
            self.sentToMore = sentToMore
            self.snippet = snippet
            self.snoozedUntil = snoozedUntil
            self.subject = subject
            self.threadId = threadId
            self.uid = uid
            self.when = when
        }

        enum CodingKeys: String, CodingKey {
            case account
            case bucket
            case commitmentWorthy = "commitment_worthy"
            case flagged
            case folder
            case fromAddr = "from_addr"
            case fromName = "from_name"
            case hasAttach = "has_attach"
            case importance
            case isJunk = "is_junk"
            case key
            case needsYou = "needs_you"
            case replyWorthy = "reply_worthy"
            case safeToFile = "safe_to_file"
            case seen
            case sentTo = "sent_to"
            case sentToMore = "sent_to_more"
            case snippet
            case snoozedUntil = "snoozed_until"
            case subject
            case threadId = "thread_id"
            case uid
            case when
        }
    }

    /// `Calls.spending`: what AI cost over the last seven days, today's among them, and this month so far.
    public struct Spending: Codable, Hashable, Sendable {
        public var month: PeriodSpent
        public var ok: Bool
        public var week: PeriodSpent

        public init(month: PeriodSpent, ok: Bool, week: PeriodSpent) {
            self.month = month
            self.ok = ok
            self.week = week
        }
    }

    public struct Standing: Codable, Hashable, Sendable {
        public var accounts: [AccountStanding]
        public var generated: String
        public var ok: Bool

        public init(accounts: [AccountStanding], generated: String, ok: Bool) {
            self.accounts = accounts
            self.generated = generated
            self.ok = ok
        }
    }

    public struct Stats: Codable, Hashable, Sendable {
        public var accounts: [AccountStats]
        public var buckets: [Bucket]
        public var generated: String
        public var totals: Totals

        public init(accounts: [AccountStats], buckets: [Bucket], generated: String, totals: Totals) {
            self.accounts = accounts
            self.buckets = buckets
            self.generated = generated
            self.totals = totals
        }
    }

    /// A meeting the text proposes, its `start` and `end` a day or a time (`kernel.proposed`).
    public struct SuggestedEvent: Codable, Hashable, Sendable {
        public var allDay: Bool
        public var end: String?
        public var place: String
        public var start: String
        public var title: String

        public init(allDay: Bool, end: String? = nil, place: String, start: String, title: String) {
            self.allDay = allDay
            self.end = end
            self.place = place
            self.start = start
            self.title = title
        }

        enum CodingKeys: String, CodingKey {
            case allDay = "all_day"
            case end
            case place
            case start
            case title
        }
    }

    public struct SuggestedTask: Codable, Hashable, Sendable {
        public var due: String?
        public var title: String

        public init(due: String? = nil, title: String) {
            self.due = due
            self.title = title
        }
    }

    public struct Suggestion: Codable, Hashable, Sendable {
        public var addr: String
        public var n: Int
        public var name: String

        public init(addr: String, n: Int, name: String) {
            self.addr = addr
            self.n = n
            self.name = name
        }
    }

    public struct Suggestions: Codable, Hashable, Sendable {
        public var items: [Suggestion]
        public var ok: Bool

        public init(items: [Suggestion], ok: Bool) {
            self.items = items
            self.ok = ok
        }
    }

    /// A message by its key, or a thread by its account and id.
    public struct SummarizeBody: Codable, Hashable, Sendable {
        public var account: String?
        public var folder: String?
        public var force: Bool?
        public var key: Int?
        public var threadId: String?
        public var uid: Int?

        public init(account: String? = nil, folder: String? = nil, force: Bool? = nil, key: Int? = nil, threadId: String? = nil, uid: Int? = nil) {
            self.account = account
            self.folder = folder
            self.force = force
            self.key = key
            self.threadId = threadId
            self.uid = uid
        }

        enum CodingKeys: String, CodingKey {
            case account
            case folder
            case force
            case key
            case threadId = "thread_id"
            case uid
        }
    }

    public struct Summary: Codable, Hashable, Sendable {
        public var account: String
        public var action: String
        public var cached: Bool
        public var createdAt: String
        public var messageCount: Int
        public var model: String
        public var ok: Bool
        public var participants: [String]
        public var points: [String]
        public var scope: String
        public var stale: Bool
        public var threadId: String
        public var tldr: String

        public init(account: String, action: String, cached: Bool, createdAt: String, messageCount: Int, model: String, ok: Bool, participants: [String], points: [String], scope: String, stale: Bool, threadId: String, tldr: String) {
            self.account = account
            self.action = action
            self.cached = cached
            self.createdAt = createdAt
            self.messageCount = messageCount
            self.model = model
            self.ok = ok
            self.participants = participants
            self.points = points
            self.scope = scope
            self.stale = stale
            self.threadId = threadId
            self.tldr = tldr
        }

        enum CodingKeys: String, CodingKey {
            case account
            case action
            case cached
            case createdAt = "created_at"
            case messageCount = "message_count"
            case model
            case ok
            case participants
            case points
            case scope
            case stale
            case threadId = "thread_id"
            case tldr
        }
    }

    public struct SweepBody: Codable, Hashable, Sendable {
        public var account: String?
        public var dryRun: Bool?
        public var undoGroup: String?

        public init(account: String? = nil, dryRun: Bool? = nil, undoGroup: String? = nil) {
            self.account = account
            self.dryRun = dryRun
            self.undoGroup = undoGroup
        }

        enum CodingKeys: String, CodingKey {
            case account
            case dryRun = "dry_run"
            case undoGroup = "undo_group"
        }
    }

    /// A junk bucket a sweep would move: how much of it, the senders with the most and the newest subjects, a few of each.
    public struct SweepBucket: Codable, Hashable, Sendable {
        public var bucket: String
        public var count: Int
        public var senders: [SweepSender]
        public var subjects: [String]

        public init(bucket: String, count: Int, senders: [SweepSender], subjects: [String]) {
            self.bucket = bucket
            self.count = count
            self.senders = senders
            self.subjects = subjects
        }
    }

    /// One of the senders with the most of a bucket a sweep would move.
    public struct SweepSender: Codable, Hashable, Sendable {
        public var count: Int
        public var fromAddr: String

        public init(count: Int, fromAddr: String) {
            self.count = count
            self.fromAddr = fromAddr
        }

        enum CodingKeys: String, CodingKey {
            case count
            case fromAddr = "from_addr"
        }
    }

    public struct Swept: Codable, Hashable, Sendable {
        public var dryRun: Bool
        public var generated: String
        public var ok: Bool
        public var results: [AccountSwept]
        public var undoGroup: String?

        public init(dryRun: Bool, generated: String, ok: Bool, results: [AccountSwept], undoGroup: String? = nil) {
            self.dryRun = dryRun
            self.generated = generated
            self.ok = ok
            self.results = results
            self.undoGroup = undoGroup
        }

        enum CodingKeys: String, CodingKey {
            case dryRun = "dry_run"
            case generated
            case ok
            case results
            case undoGroup = "undo_group"
        }
    }

    public struct SyncRetried: Codable, Hashable, Sendable {
        public var account: String
        public var ok: Bool

        public init(account: String, ok: Bool) {
            self.account = account
            self.ok = ok
        }
    }

    public struct SyncRetryBody: Codable, Hashable, Sendable {
        public var account: String?

        public init(account: String? = nil) {
            self.account = account
        }
    }

    public struct SyncStarted: Codable, Hashable, Sendable {
        public var msg: String
        public var ok: Bool

        public init(msg: String, ok: Bool) {
            self.msg = msg
            self.ok = ok
        }
    }

    public struct SyncState: Codable, Hashable, Sendable {
        public var last: String?
        public var result: String
        public var running: Bool

        public init(last: String? = nil, result: String, running: Bool) {
            self.last = last
            self.result = result
            self.running = running
        }
    }

    /// A task and the copy its message has now: `key`, `folder` and `uid` are null once none is left.
    public struct Task: Codable, Hashable, Sendable {
        public var account: String
        public var done: Bool
        public var due: String?
        public var folder: String?
        public var fromAddr: String?
        public var fromName: String?
        public var id: Int
        public var key: Int?
        public var subject: String?
        public var threadId: String?
        public var title: String
        public var uid: Int?

        public init(account: String, done: Bool, due: String? = nil, folder: String? = nil, fromAddr: String? = nil, fromName: String? = nil, id: Int, key: Int? = nil, subject: String? = nil, threadId: String? = nil, title: String, uid: Int? = nil) {
            self.account = account
            self.done = done
            self.due = due
            self.folder = folder
            self.fromAddr = fromAddr
            self.fromName = fromName
            self.id = id
            self.key = key
            self.subject = subject
            self.threadId = threadId
            self.title = title
            self.uid = uid
        }

        enum CodingKeys: String, CodingKey {
            case account
            case done
            case due
            case folder
            case fromAddr = "from_addr"
            case fromName = "from_name"
            case id
            case key
            case subject
            case threadId = "thread_id"
            case title
            case uid
        }
    }

    public struct TaskAdded: Codable, Hashable, Sendable {
        public var ok: Bool
        public var task: Task

        public init(ok: Bool, task: Task) {
            self.ok = ok
            self.task = task
        }
    }

    /// A task someone confirmed from the message `key` names, as they left it.
    public struct TaskBody: Codable, Hashable, Sendable {
        public var due: String?
        public var folder: String?
        public var key: Int?
        public var title: String?
        public var uid: Int?

        public init(due: String? = nil, folder: String? = nil, key: Int? = nil, title: String? = nil, uid: Int? = nil) {
            self.due = due
            self.folder = folder
            self.key = key
            self.title = title
            self.uid = uid
        }
    }

    public struct TaskDoneBody: Codable, Hashable, Sendable {
        public var done: Bool?
        public var id: Int

        public init(done: Bool? = nil, id: Int) {
            self.done = done
            self.id = id
        }
    }

    public struct TaskGone: Codable, Hashable, Sendable {
        public var ok: Bool

        public init(ok: Bool) {
            self.ok = ok
        }
    }

    public struct TaskList: Codable, Hashable, Sendable {
        public var items: [Task]
        public var ok: Bool

        public init(items: [Task], ok: Bool) {
            self.items = items
            self.ok = ok
        }
    }

    public struct TaskRemoveBody: Codable, Hashable, Sendable {
        public var id: Int

        public init(id: Int) {
            self.id = id
        }
    }

    public enum Theme: String, Codable, Hashable, Sendable, CaseIterable {
        case auto
        case day
        case dusk
        case night
    }

    public struct Thread: Codable, Hashable, Sendable {
        public var account: String
        public var count: Int
        public var items: [ThreadMessage]
        public var ok: Bool
        public var replyKey: Int?
        public var threadId: String

        public init(account: String, count: Int, items: [ThreadMessage], ok: Bool, replyKey: Int? = nil, threadId: String) {
            self.account = account
            self.count = count
            self.items = items
            self.ok = ok
            self.replyKey = replyKey
            self.threadId = threadId
        }

        enum CodingKeys: String, CodingKey {
            case account
            case count
            case items
            case ok
            case replyKey = "reply_key"
            case threadId = "thread_id"
        }
    }

    public struct ThreadMessage: Codable, Hashable, Sendable {
        public var answered: Bool
        public var flagged: Bool
        public var folder: String
        public var fromAddr: String?
        public var fromName: String?
        public var hasAttach: Bool
        public var isJunk: Bool
        public var key: Int
        public var seen: Bool
        public var sentTo: [Addressee]
        public var sentToMore: Int
        public var snippet: String
        public var subject: String
        public var uid: Int
        public var when: String

        public init(answered: Bool, flagged: Bool, folder: String, fromAddr: String? = nil, fromName: String? = nil, hasAttach: Bool, isJunk: Bool, key: Int, seen: Bool, sentTo: [Addressee], sentToMore: Int, snippet: String, subject: String, uid: Int, when: String) {
            self.answered = answered
            self.flagged = flagged
            self.folder = folder
            self.fromAddr = fromAddr
            self.fromName = fromName
            self.hasAttach = hasAttach
            self.isJunk = isJunk
            self.key = key
            self.seen = seen
            self.sentTo = sentTo
            self.sentToMore = sentToMore
            self.snippet = snippet
            self.subject = subject
            self.uid = uid
            self.when = when
        }

        enum CodingKeys: String, CodingKey {
            case answered
            case flagged
            case folder
            case fromAddr = "from_addr"
            case fromName = "from_name"
            case hasAttach = "has_attach"
            case isJunk = "is_junk"
            case key
            case seen
            case sentTo = "sent_to"
            case sentToMore = "sent_to_more"
            case snippet
            case subject
            case uid
            case when
        }
    }

    public struct TidyItem: Codable, Hashable, Sendable {
        public var subject: String?
        public var who: String?

        public init(subject: String? = nil, who: String? = nil) {
            self.subject = subject
            self.who = who
        }
    }

    /// The newest run of Tidy that can still be taken back, or no group at all.
    public struct TidyRun: Codable, Hashable, Sendable {
        public var filed: Int
        public var group: String?
        public var items: [TidyItem]
        public var ok: Bool

        public init(filed: Int, group: String? = nil, items: [TidyItem], ok: Bool) {
            self.filed = filed
            self.group = group
            self.items = items
            self.ok = ok
        }
    }

    public struct TidyUndoBody: Codable, Hashable, Sendable {
        public var group: String?

        public init(group: String? = nil) {
            self.group = group
        }
    }

    public struct Totals: Codable, Hashable, Sendable {
        public var inInbox: Int
        public var junk: Int
        public var keep: Int

        public init(inInbox: Int, junk: Int, keep: Int) {
            self.inInbox = inInbox
            self.junk = junk
            self.keep = keep
        }

        enum CodingKeys: String, CodingKey {
            case inInbox = "in_inbox"
            case junk
            case keep
        }
    }

    public struct Trusted: Codable, Hashable, Sendable {
        public var account: String
        public var ok: Bool

        public init(account: String, ok: Bool) {
            self.account = account
            self.ok = ok
        }
    }

    /// A saved account by its address, or else a draft. Nothing else here reaches a server: a saved account goes to its saved
    /// endpoint with its saved credential, whatever else the request names.
    public struct TryBody: Codable, Hashable, Sendable {
        public var draft: AccountDraft?
        public var email: String?

        public init(draft: AccountDraft? = nil, email: String? = nil) {
            self.draft = draft
            self.email = email
        }
    }

    /// The model didn't answer (`Unreachable`), and `retry` says asking again can help.
    public struct Unavailable: Codable, Hashable, Sendable {
        public var aiOff: Bool?
        public var busy: Bool?
        public var detail: String?
        public var limitMicros: Int?
        public var overLimit: Bool?
        public var readOnly: Bool?
        public var retry: Bool?
        public var spentMicros: Int?
        public var status: Int
        public var title: String
        public var type: String

        public init(aiOff: Bool? = nil, busy: Bool? = nil, detail: String? = nil, limitMicros: Int? = nil, overLimit: Bool? = nil, readOnly: Bool? = nil, retry: Bool? = nil, spentMicros: Int? = nil, status: Int, title: String, type: String) {
            self.aiOff = aiOff
            self.busy = busy
            self.detail = detail
            self.limitMicros = limitMicros
            self.overLimit = overLimit
            self.readOnly = readOnly
            self.retry = retry
            self.spentMicros = spentMicros
            self.status = status
            self.title = title
            self.type = type
        }

        enum CodingKeys: String, CodingKey {
            case aiOff = "ai_off"
            case busy
            case detail
            case limitMicros = "limit_micros"
            case overLimit = "over_limit"
            case readOnly = "read_only"
            case retry
            case spentMicros = "spent_micros"
            case status
            case title
            case type
        }
    }

    public struct Undone: Codable, Hashable, Sendable {
        public var account: String?
        public var failed: Int?
        public var folder: String?
        public var label: String?
        public var ok: Bool
        public var remaining: Int
        public var reversed: Int?

        public init(account: String? = nil, failed: Int? = nil, folder: String? = nil, label: String? = nil, ok: Bool, remaining: Int, reversed: Int? = nil) {
            self.account = account
            self.failed = failed
            self.folder = folder
            self.label = label
            self.ok = ok
            self.remaining = remaining
            self.reversed = reversed
        }
    }

    public struct Unsnoozed: Codable, Hashable, Sendable {
        public var count: Int
        public var ok: Bool

        public init(count: Int, ok: Bool) {
            self.count = count
            self.ok = ok
        }
    }

    public struct Upcoming: Codable, Hashable, Sendable {
        public var items: [UpcomingEvent]
        public var ok: Bool

        public init(items: [UpcomingEvent], ok: Bool) {
            self.items = items
            self.ok = ok
        }
    }

    public struct UpcomingEvent: Codable, Hashable, Sendable {
        public var account: String
        public var allDay: Bool
        public var end: String
        public var folder: String
        public var invite: Bool
        public var key: Int
        public var location: String
        public var organizer: String
        public var recurrence: String
        public var start: String
        public var summary: String
        public var threadId: String?
        public var uid: Int

        public init(account: String, allDay: Bool, end: String, folder: String, invite: Bool, key: Int, location: String, organizer: String, recurrence: String, start: String, summary: String, threadId: String? = nil, uid: Int) {
            self.account = account
            self.allDay = allDay
            self.end = end
            self.folder = folder
            self.invite = invite
            self.key = key
            self.location = location
            self.organizer = organizer
            self.recurrence = recurrence
            self.start = start
            self.summary = summary
            self.threadId = threadId
            self.uid = uid
        }

        enum CodingKeys: String, CodingKey {
            case account
            case allDay = "all_day"
            case end
            case folder
            case invite
            case key
            case location
            case organizer
            case recurrence
            case start
            case summary
            case threadId = "thread_id"
            case uid
        }
    }

    public enum Verdict: String, Codable, Hashable, Sendable, CaseIterable {
        case keep
        case block
    }

    public struct ViewChange: Codable, Hashable, Sendable {
        public var id: String?
        public var name: String?
        public var q: String?
        public var verb: ViewVerb?

        public init(id: String? = nil, name: String? = nil, q: String? = nil, verb: ViewVerb? = nil) {
            self.id = id
            self.name = name
            self.q = q
            self.verb = verb
        }
    }

    /// How many rows each view that isn't a folder holds.
    public struct ViewCounts: Codable, Hashable, Sendable {
        public var muted: Int
        public var ok: Bool
        public var replylater: Int
        public var snoozed: Int
        public var waiting: Int

        public init(muted: Int, ok: Bool, replylater: Int, snoozed: Int, waiting: Int) {
            self.muted = muted
            self.ok = ok
            self.replylater = replylater
            self.snoozed = snoozed
            self.waiting = waiting
        }
    }

    public enum ViewVerb: String, Codable, Hashable, Sendable, CaseIterable {
        case create
        case delete
    }

    public struct Views: Codable, Hashable, Sendable {
        public var ok: Bool
        public var views: [ListedView]

        public init(ok: Bool, views: [ListedView]) {
            self.ok = ok
            self.views = views
        }
    }

    public struct WaitingDraft: Codable, Hashable, Sendable {
        public var account: String
        public var attempts: Int
        public var discarding: Bool
        public var id: Int
        public var kept: Bool
        public var savedAt: Int
        public var subject: String

        public init(account: String, attempts: Int, discarding: Bool, id: Int, kept: Bool, savedAt: Int, subject: String) {
            self.account = account
            self.attempts = attempts
            self.discarding = discarding
            self.id = id
            self.kept = kept
            self.savedAt = savedAt
            self.subject = subject
        }

        enum CodingKeys: String, CodingKey {
            case account
            case attempts
            case discarding
            case id
            case kept
            case savedAt = "saved_at"
            case subject
        }
    }

    public struct WaitingListing: Codable, Hashable, Sendable {
        public var account: String?
        public var folder: String
        public var hasMore: Bool
        public var items: [WaitingRow]
        public var offset: Int
        public var ok: Bool
        public var total: Int
        public var waiting: Bool

        public init(account: String? = nil, folder: String, hasMore: Bool, items: [WaitingRow], offset: Int, ok: Bool, total: Int, waiting: Bool) {
            self.account = account
            self.folder = folder
            self.hasMore = hasMore
            self.items = items
            self.offset = offset
            self.ok = ok
            self.total = total
            self.waiting = waiting
        }

        enum CodingKeys: String, CodingKey {
            case account
            case folder
            case hasMore = "has_more"
            case items
            case offset
            case ok
            case total
            case waiting
        }

        /// The keys it names, and those it requires, which a union's decoding goes by.
        static let named: Set<String> = ["account", "folder", "has_more", "items", "offset", "ok", "total", "waiting"]
        static let required: Set<String> = ["account", "folder", "has_more", "items", "offset", "ok", "total", "waiting"]
    }

    public struct WaitingOnOthers: Codable, Hashable, Sendable {
        public var items: [WaitingRow]
        public var ok: Bool

        public init(items: [WaitingRow], ok: Bool) {
            self.items = items
            self.ok = ok
        }
    }

    public struct WaitingRow: Codable, Hashable, Sendable {
        public var account: String
        public var bucket: String?
        public var count: Int
        public var flagged: Bool
        public var folder: String
        public var fromAddr: String?
        public var fromName: String?
        public var hasAttach: Bool
        public var isJunk: Bool
        public var key: Int
        public var kind: String
        public var seen: Bool
        public var sentTo: [Addressee]
        public var sentToMore: Int
        public var snippet: String
        public var subject: String
        public var threadId: String?
        public var to: String
        public var ts: Int?
        public var uid: Int
        public var waiting: Bool
        public var waitingLabel: String
        public var when: String

        public init(account: String, bucket: String? = nil, count: Int, flagged: Bool, folder: String, fromAddr: String? = nil, fromName: String? = nil, hasAttach: Bool, isJunk: Bool, key: Int, kind: String, seen: Bool, sentTo: [Addressee], sentToMore: Int, snippet: String, subject: String, threadId: String? = nil, to: String, ts: Int? = nil, uid: Int, waiting: Bool, waitingLabel: String, when: String) {
            self.account = account
            self.bucket = bucket
            self.count = count
            self.flagged = flagged
            self.folder = folder
            self.fromAddr = fromAddr
            self.fromName = fromName
            self.hasAttach = hasAttach
            self.isJunk = isJunk
            self.key = key
            self.kind = kind
            self.seen = seen
            self.sentTo = sentTo
            self.sentToMore = sentToMore
            self.snippet = snippet
            self.subject = subject
            self.threadId = threadId
            self.to = to
            self.ts = ts
            self.uid = uid
            self.waiting = waiting
            self.waitingLabel = waitingLabel
            self.when = when
        }

        enum CodingKeys: String, CodingKey {
            case account
            case bucket
            case count
            case flagged
            case folder
            case fromAddr = "from_addr"
            case fromName = "from_name"
            case hasAttach = "has_attach"
            case isJunk = "is_junk"
            case key
            case kind
            case seen
            case sentTo = "sent_to"
            case sentToMore = "sent_to_more"
            case snippet
            case subject
            case threadId = "thread_id"
            case to
            case ts
            case uid
            case waiting
            case waitingLabel = "waiting_label"
            case when
        }
    }

    /// What a person is shown of a sender's way out before confirming: the way, and only where it goes.
    public struct WayOut: Codable, Hashable, Sendable {
        public var httpsHost: String?
        public var mailtoTo: String?
        public var method: String

        public init(httpsHost: String? = nil, mailtoTo: String? = nil, method: String) {
            self.httpsHost = httpsHost
            self.mailtoTo = mailtoTo
            self.method = method
        }

        enum CodingKeys: String, CodingKey {
            case httpsHost = "https_host"
            case mailtoTo = "mailto_to"
            case method
        }
    }

    public enum CredentialShownRef: Codable, Hashable, Sendable {
        case string(String)
        case strings([String])

        public init(from decoder: any Decoder) throws {
            if let value = try? String(from: decoder) {
                self = .string(value)
                return
            }
            if let value = try? [String](from: decoder) {
                self = .strings(value)
                return
            }
            throw Union.noMember(CredentialShownRef.self, decoder)
        }

        public func encode(to encoder: any Encoder) throws {
            switch self {
            case .string(let value): try value.encode(to: encoder)
            case .strings(let value): try value.encode(to: encoder)
            }
        }
    }

    public enum DeltaBodySince: Codable, Hashable, Sendable {
        case int(Int)
        case string(String)

        public init(from decoder: any Decoder) throws {
            if let value = try? Int(from: decoder) {
                self = .int(value)
                return
            }
            if let value = try? String(from: decoder) {
                self = .string(value)
                return
            }
            throw Union.noMember(DeltaBodySince.self, decoder)
        }

        public func encode(to encoder: any Encoder) throws {
            switch self {
            case .int(let value): try value.encode(to: encoder)
            case .string(let value): try value.encode(to: encoder)
            }
        }
    }

    public enum DraftBodyBcc: Codable, Hashable, Sendable {
        case string(String)
        case strings([String])

        public init(from decoder: any Decoder) throws {
            if let value = try? String(from: decoder) {
                self = .string(value)
                return
            }
            if let value = try? [String](from: decoder) {
                self = .strings(value)
                return
            }
            throw Union.noMember(DraftBodyBcc.self, decoder)
        }

        public func encode(to encoder: any Encoder) throws {
            switch self {
            case .string(let value): try value.encode(to: encoder)
            case .strings(let value): try value.encode(to: encoder)
            }
        }
    }

    public enum DraftBodyCc: Codable, Hashable, Sendable {
        case string(String)
        case strings([String])

        public init(from decoder: any Decoder) throws {
            if let value = try? String(from: decoder) {
                self = .string(value)
                return
            }
            if let value = try? [String](from: decoder) {
                self = .strings(value)
                return
            }
            throw Union.noMember(DraftBodyCc.self, decoder)
        }

        public func encode(to encoder: any Encoder) throws {
            switch self {
            case .string(let value): try value.encode(to: encoder)
            case .strings(let value): try value.encode(to: encoder)
            }
        }
    }

    public enum DraftBodyTo: Codable, Hashable, Sendable {
        case string(String)
        case strings([String])

        public init(from decoder: any Decoder) throws {
            if let value = try? String(from: decoder) {
                self = .string(value)
                return
            }
            if let value = try? [String](from: decoder) {
                self = .strings(value)
                return
            }
            throw Union.noMember(DraftBodyTo.self, decoder)
        }

        public func encode(to encoder: any Encoder) throws {
            switch self {
            case .string(let value): try value.encode(to: encoder)
            case .strings(let value): try value.encode(to: encoder)
            }
        }
    }

    public enum SendBodyBcc: Codable, Hashable, Sendable {
        case string(String)
        case strings([String])

        public init(from decoder: any Decoder) throws {
            if let value = try? String(from: decoder) {
                self = .string(value)
                return
            }
            if let value = try? [String](from: decoder) {
                self = .strings(value)
                return
            }
            throw Union.noMember(SendBodyBcc.self, decoder)
        }

        public func encode(to encoder: any Encoder) throws {
            switch self {
            case .string(let value): try value.encode(to: encoder)
            case .strings(let value): try value.encode(to: encoder)
            }
        }
    }

    public enum SendBodyCc: Codable, Hashable, Sendable {
        case string(String)
        case strings([String])

        public init(from decoder: any Decoder) throws {
            if let value = try? String(from: decoder) {
                self = .string(value)
                return
            }
            if let value = try? [String](from: decoder) {
                self = .strings(value)
                return
            }
            throw Union.noMember(SendBodyCc.self, decoder)
        }

        public func encode(to encoder: any Encoder) throws {
            switch self {
            case .string(let value): try value.encode(to: encoder)
            case .strings(let value): try value.encode(to: encoder)
            }
        }
    }

    public enum SendBodyTo: Codable, Hashable, Sendable {
        case string(String)
        case strings([String])

        public init(from decoder: any Decoder) throws {
            if let value = try? String(from: decoder) {
                self = .string(value)
                return
            }
            if let value = try? [String](from: decoder) {
                self = .strings(value)
                return
            }
            throw Union.noMember(SendBodyTo.self, decoder)
        }

        public func encode(to encoder: any Encoder) throws {
            switch self {
            case .string(let value): try value.encode(to: encoder)
            case .strings(let value): try value.encode(to: encoder)
            }
        }
    }

    public enum SnoozeBodyUntil: Codable, Hashable, Sendable {
        case int(Int)
        case string(String)

        public init(from decoder: any Decoder) throws {
            if let value = try? Int(from: decoder) {
                self = .int(value)
                return
            }
            if let value = try? String(from: decoder) {
                self = .string(value)
                return
            }
            throw Union.noMember(SnoozeBodyUntil.self, decoder)
        }

        public func encode(to encoder: any Encoder) throws {
            switch self {
            case .int(let value): try value.encode(to: encoder)
            case .string(let value): try value.encode(to: encoder)
            }
        }
    }

    public enum AiDraftAheadAnswer: Codable, Hashable, Sendable {
        case draftAheadStatus(DraftAheadStatus)
        case draftAheadReady(DraftAheadReady)

        public init(from decoder: any Decoder) throws {
            let keys = Union.keys(of: decoder)
            if Union.fits(keys, named: DraftAheadStatus.named, required: DraftAheadStatus.required), let value = try? DraftAheadStatus(from: decoder) {
                self = .draftAheadStatus(value)
                return
            }
            if Union.fits(keys, named: DraftAheadReady.named, required: DraftAheadReady.required), let value = try? DraftAheadReady(from: decoder) {
                self = .draftAheadReady(value)
                return
            }
            if let value = try? DraftAheadStatus(from: decoder) {
                self = .draftAheadStatus(value)
                return
            }
            if let value = try? DraftAheadReady(from: decoder) {
                self = .draftAheadReady(value)
                return
            }
            throw Union.noMember(AiDraftAheadAnswer.self, decoder)
        }

        public func encode(to encoder: any Encoder) throws {
            switch self {
            case .draftAheadStatus(let value): try value.encode(to: encoder)
            case .draftAheadReady(let value): try value.encode(to: encoder)
            }
        }
    }

    public enum BriefingAnswer: Codable, Hashable, Sendable {
        case brief(Brief)
        case briefMissing(BriefMissing)

        public init(from decoder: any Decoder) throws {
            let keys = Union.keys(of: decoder)
            if Union.fits(keys, named: Brief.named, required: Brief.required), let value = try? Brief(from: decoder) {
                self = .brief(value)
                return
            }
            if Union.fits(keys, named: BriefMissing.named, required: BriefMissing.required), let value = try? BriefMissing(from: decoder) {
                self = .briefMissing(value)
                return
            }
            if let value = try? Brief(from: decoder) {
                self = .brief(value)
                return
            }
            if let value = try? BriefMissing(from: decoder) {
                self = .briefMissing(value)
                return
            }
            throw Union.noMember(BriefingAnswer.self, decoder)
        }

        public func encode(to encoder: any Encoder) throws {
            switch self {
            case .brief(let value): try value.encode(to: encoder)
            case .briefMissing(let value): try value.encode(to: encoder)
            }
        }
    }

    public enum BriefingSinceAnswer: Codable, Hashable, Sendable {
        case delta(Delta)
        case deltaEmpty(DeltaEmpty)
        case deltaMissing(DeltaMissing)

        public init(from decoder: any Decoder) throws {
            let keys = Union.keys(of: decoder)
            if Union.fits(keys, named: Delta.named, required: Delta.required), let value = try? Delta(from: decoder) {
                self = .delta(value)
                return
            }
            if Union.fits(keys, named: DeltaEmpty.named, required: DeltaEmpty.required), let value = try? DeltaEmpty(from: decoder) {
                self = .deltaEmpty(value)
                return
            }
            if Union.fits(keys, named: DeltaMissing.named, required: DeltaMissing.required), let value = try? DeltaMissing(from: decoder) {
                self = .deltaMissing(value)
                return
            }
            if let value = try? Delta(from: decoder) {
                self = .delta(value)
                return
            }
            if let value = try? DeltaEmpty(from: decoder) {
                self = .deltaEmpty(value)
                return
            }
            if let value = try? DeltaMissing(from: decoder) {
                self = .deltaMissing(value)
                return
            }
            throw Union.noMember(BriefingSinceAnswer.self, decoder)
        }

        public func encode(to encoder: any Encoder) throws {
            switch self {
            case .delta(let value): try value.encode(to: encoder)
            case .deltaEmpty(let value): try value.encode(to: encoder)
            case .deltaMissing(let value): try value.encode(to: encoder)
            }
        }
    }

    public enum MessageBodyAnswer: Codable, Hashable, Sendable {
        case messageOpened(MessageOpened)
        case prefetched(Prefetched)

        public init(from decoder: any Decoder) throws {
            let keys = Union.keys(of: decoder)
            if Union.fits(keys, named: MessageOpened.named, required: MessageOpened.required), let value = try? MessageOpened(from: decoder) {
                self = .messageOpened(value)
                return
            }
            if Union.fits(keys, named: Prefetched.named, required: Prefetched.required), let value = try? Prefetched(from: decoder) {
                self = .prefetched(value)
                return
            }
            if let value = try? MessageOpened(from: decoder) {
                self = .messageOpened(value)
                return
            }
            if let value = try? Prefetched(from: decoder) {
                self = .prefetched(value)
                return
            }
            throw Union.noMember(MessageBodyAnswer.self, decoder)
        }

        public func encode(to encoder: any Encoder) throws {
            switch self {
            case .messageOpened(let value): try value.encode(to: encoder)
            case .prefetched(let value): try value.encode(to: encoder)
            }
        }
    }

    public enum MessagesAnswer: Codable, Hashable, Sendable {
        case folderListing(FolderListing)
        case snoozedListing(SnoozedListing)
        case mutedListing(MutedListing)
        case waitingListing(WaitingListing)
        case replyLaterListing(ReplyLaterListing)

        public init(from decoder: any Decoder) throws {
            let keys = Union.keys(of: decoder)
            if Union.fits(keys, named: FolderListing.named, required: FolderListing.required), let value = try? FolderListing(from: decoder) {
                self = .folderListing(value)
                return
            }
            if Union.fits(keys, named: SnoozedListing.named, required: SnoozedListing.required), let value = try? SnoozedListing(from: decoder) {
                self = .snoozedListing(value)
                return
            }
            if Union.fits(keys, named: MutedListing.named, required: MutedListing.required), let value = try? MutedListing(from: decoder) {
                self = .mutedListing(value)
                return
            }
            if Union.fits(keys, named: WaitingListing.named, required: WaitingListing.required), let value = try? WaitingListing(from: decoder) {
                self = .waitingListing(value)
                return
            }
            if Union.fits(keys, named: ReplyLaterListing.named, required: ReplyLaterListing.required), let value = try? ReplyLaterListing(from: decoder) {
                self = .replyLaterListing(value)
                return
            }
            if let value = try? FolderListing(from: decoder) {
                self = .folderListing(value)
                return
            }
            if let value = try? SnoozedListing(from: decoder) {
                self = .snoozedListing(value)
                return
            }
            if let value = try? MutedListing(from: decoder) {
                self = .mutedListing(value)
                return
            }
            if let value = try? WaitingListing(from: decoder) {
                self = .waitingListing(value)
                return
            }
            if let value = try? ReplyLaterListing(from: decoder) {
                self = .replyLaterListing(value)
                return
            }
            throw Union.noMember(MessagesAnswer.self, decoder)
        }

        public func encode(to encoder: any Encoder) throws {
            switch self {
            case .folderListing(let value): try value.encode(to: encoder)
            case .snoozedListing(let value): try value.encode(to: encoder)
            case .mutedListing(let value): try value.encode(to: encoder)
            case .waitingListing(let value): try value.encode(to: encoder)
            case .replyLaterListing(let value): try value.encode(to: encoder)
            }
        }
    }

    public enum ReviewAnswer: Codable, Hashable, Sendable {
        case review(Review)
        case reviewMissing(ReviewMissing)

        public init(from decoder: any Decoder) throws {
            let keys = Union.keys(of: decoder)
            if Union.fits(keys, named: Review.named, required: Review.required), let value = try? Review(from: decoder) {
                self = .review(value)
                return
            }
            if Union.fits(keys, named: ReviewMissing.named, required: ReviewMissing.required), let value = try? ReviewMissing(from: decoder) {
                self = .reviewMissing(value)
                return
            }
            if let value = try? Review(from: decoder) {
                self = .review(value)
                return
            }
            if let value = try? ReviewMissing(from: decoder) {
                self = .reviewMissing(value)
                return
            }
            throw Union.noMember(ReviewAnswer.self, decoder)
        }

        public func encode(to encoder: any Encoder) throws {
            switch self {
            case .review(let value): try value.encode(to: encoder)
            case .reviewMissing(let value): try value.encode(to: encoder)
            }
        }
    }

    public enum SearchAnswer: Codable, Hashable, Sendable {
        case searchPage(SearchPage)
        case nothingAsked(NothingAsked)

        public init(from decoder: any Decoder) throws {
            let keys = Union.keys(of: decoder)
            if Union.fits(keys, named: SearchPage.named, required: SearchPage.required), let value = try? SearchPage(from: decoder) {
                self = .searchPage(value)
                return
            }
            if Union.fits(keys, named: NothingAsked.named, required: NothingAsked.required), let value = try? NothingAsked(from: decoder) {
                self = .nothingAsked(value)
                return
            }
            if let value = try? SearchPage(from: decoder) {
                self = .searchPage(value)
                return
            }
            if let value = try? NothingAsked(from: decoder) {
                self = .nothingAsked(value)
                return
            }
            throw Union.noMember(SearchAnswer.self, decoder)
        }

        public func encode(to encoder: any Encoder) throws {
            switch self {
            case .searchPage(let value): try value.encode(to: encoder)
            case .nothingAsked(let value): try value.encode(to: encoder)
            }
        }
    }

    public enum AccountsTestAnswer: Codable, Hashable, Sendable {
        case loggedIn(LoggedIn)
        case couldntLogIn(CouldntLogIn)

        public init(from decoder: any Decoder) throws {
            let keys = Union.keys(of: decoder)
            if Union.fits(keys, named: LoggedIn.named, required: LoggedIn.required), let value = try? LoggedIn(from: decoder) {
                self = .loggedIn(value)
                return
            }
            if Union.fits(keys, named: CouldntLogIn.named, required: CouldntLogIn.required), let value = try? CouldntLogIn(from: decoder) {
                self = .couldntLogIn(value)
                return
            }
            if let value = try? LoggedIn(from: decoder) {
                self = .loggedIn(value)
                return
            }
            if let value = try? CouldntLogIn(from: decoder) {
                self = .couldntLogIn(value)
                return
            }
            throw Union.noMember(AccountsTestAnswer.self, decoder)
        }

        public func encode(to encoder: any Encoder) throws {
            switch self {
            case .loggedIn(let value): try value.encode(to: encoder)
            case .couldntLogIn(let value): try value.encode(to: encoder)
            }
        }
    }

    public enum AiDoPlanAnswer: Codable, Hashable, Sendable {
        case planPreview(PlanPreview)
        case planIsASearch(PlanIsASearch)

        public init(from decoder: any Decoder) throws {
            let keys = Union.keys(of: decoder)
            if Union.fits(keys, named: PlanPreview.named, required: PlanPreview.required), let value = try? PlanPreview(from: decoder) {
                self = .planPreview(value)
                return
            }
            if Union.fits(keys, named: PlanIsASearch.named, required: PlanIsASearch.required), let value = try? PlanIsASearch(from: decoder) {
                self = .planIsASearch(value)
                return
            }
            if let value = try? PlanPreview(from: decoder) {
                self = .planPreview(value)
                return
            }
            if let value = try? PlanIsASearch(from: decoder) {
                self = .planIsASearch(value)
                return
            }
            throw Union.noMember(AiDoPlanAnswer.self, decoder)
        }

        public func encode(to encoder: any Encoder) throws {
            switch self {
            case .planPreview(let value): try value.encode(to: encoder)
            case .planIsASearch(let value): try value.encode(to: encoder)
            }
        }
    }

    public enum BriefingSincePostAnswer: Codable, Hashable, Sendable {
        case delta(Delta)
        case deltaEmpty(DeltaEmpty)

        public init(from decoder: any Decoder) throws {
            let keys = Union.keys(of: decoder)
            if Union.fits(keys, named: Delta.named, required: Delta.required), let value = try? Delta(from: decoder) {
                self = .delta(value)
                return
            }
            if Union.fits(keys, named: DeltaEmpty.named, required: DeltaEmpty.required), let value = try? DeltaEmpty(from: decoder) {
                self = .deltaEmpty(value)
                return
            }
            if let value = try? Delta(from: decoder) {
                self = .delta(value)
                return
            }
            if let value = try? DeltaEmpty(from: decoder) {
                self = .deltaEmpty(value)
                return
            }
            throw Union.noMember(BriefingSincePostAnswer.self, decoder)
        }

        public func encode(to encoder: any Encoder) throws {
            switch self {
            case .delta(let value): try value.encode(to: encoder)
            case .deltaEmpty(let value): try value.encode(to: encoder)
            }
        }
    }

    public enum DraftAnswer: Codable, Hashable, Sendable {
        case draftSaved(DraftSaved)
        case nothingToSave(NothingToSave)
        case draftDiscarded(DraftDiscarded)

        public init(from decoder: any Decoder) throws {
            let keys = Union.keys(of: decoder)
            if Union.fits(keys, named: DraftSaved.named, required: DraftSaved.required), let value = try? DraftSaved(from: decoder) {
                self = .draftSaved(value)
                return
            }
            if Union.fits(keys, named: NothingToSave.named, required: NothingToSave.required), let value = try? NothingToSave(from: decoder) {
                self = .nothingToSave(value)
                return
            }
            if Union.fits(keys, named: DraftDiscarded.named, required: DraftDiscarded.required), let value = try? DraftDiscarded(from: decoder) {
                self = .draftDiscarded(value)
                return
            }
            if let value = try? DraftSaved(from: decoder) {
                self = .draftSaved(value)
                return
            }
            if let value = try? NothingToSave(from: decoder) {
                self = .nothingToSave(value)
                return
            }
            if let value = try? DraftDiscarded(from: decoder) {
                self = .draftDiscarded(value)
                return
            }
            throw Union.noMember(DraftAnswer.self, decoder)
        }

        public func encode(to encoder: any Encoder) throws {
            switch self {
            case .draftSaved(let value): try value.encode(to: encoder)
            case .nothingToSave(let value): try value.encode(to: encoder)
            case .draftDiscarded(let value): try value.encode(to: encoder)
            }
        }
    }

    /// Each route under /api/ a GET reaches.
    public enum Get {
        /// GET /api/account-health
        public enum AccountHealth {}
        /// GET /api/accounts
        public enum Accounts {}
        /// GET /api/accounts/bridge-password
        public enum AccountsBridgePassword {}
        /// GET /api/accounts/preset
        public enum AccountsPreset {}
        /// GET /api/ai/draft-ahead
        public enum AiDraftAhead {}
        /// GET /api/ai/key
        public enum AiKey {}
        /// GET /api/ai/local
        public enum AiLocal {}
        /// GET /api/ai/models
        public enum AiModels {}
        /// GET /api/ai/spending
        public enum AiSpending {}
        /// GET /api/audit
        public enum Audit {}
        /// GET /api/briefing
        public enum Briefing {}
        /// GET /api/briefing/since
        public enum BriefingSince {}
        /// GET /api/cleanup/sender
        public enum CleanupSender {}
        /// GET /api/cleanup/senders
        public enum CleanupSenders {}
        /// GET /api/contact
        public enum Contact {}
        /// GET /api/contacts
        public enum Contacts {}
        /// GET /api/debug/ai
        public enum DebugAi {}
        /// GET /api/debug/health
        public enum DebugHealth {}
        /// GET /api/debug/jobs
        public enum DebugJobs {}
        /// GET /api/debug/pool
        public enum DebugPool {}
        /// GET /api/digest
        public enum Digest {}
        /// GET /api/documents
        public enum Documents {}
        /// GET /api/events
        public enum Events {}
        /// GET /api/export
        public enum Export {}
        /// GET /api/export/archive
        public enum ExportArchive {}
        /// GET /api/export/plan
        public enum ExportPlan {}
        /// GET /api/folders
        public enum Folders {}
        /// GET /api/folders/across
        public enum FoldersAcross {}
        /// GET /api/inboxes
        public enum Inboxes {}
        /// GET /api/labels
        public enum Labels {}
        /// GET /api/message/attachment
        public enum MessageAttachment {}
        /// GET /api/message/attachment/preview.html
        public enum MessageAttachmentPreviewHtml {}
        /// GET /api/message/body
        public enum MessageBody {}
        /// GET /api/message/body.html
        public enum MessageBodyHtml {}
        /// GET /api/message/row
        public enum MessageRow {}
        /// GET /api/messages
        public enum Messages {}
        /// GET /api/outbox
        public enum Outbox {}
        /// GET /api/pending
        public enum Pending {}
        /// GET /api/preferences
        public enum Preferences {}
        /// GET /api/review
        public enum Review {}
        /// GET /api/rules
        public enum Rules {}
        /// GET /api/screener
        public enum Screener {}
        /// GET /api/search
        public enum Search {}
        /// GET /api/snippets
        public enum Snippets {}
        /// GET /api/stats
        public enum Stats {}
        /// GET /api/suggest
        public enum Suggest {}
        /// GET /api/tasks
        public enum Tasks {}
        /// GET /api/thread
        public enum Thread {}
        /// GET /api/tidy
        public enum Tidy {}
        /// GET /api/upcoming
        public enum Upcoming {}
        /// GET /api/views
        public enum Views {}
        /// GET /api/waiting-on-others
        public enum WaitingOnOthers {}
    }
    /// Each route under /api/ a POST reaches.
    public enum Post {
        /// POST /api/accounts/add
        public enum AccountsAdd {}
        /// POST /api/accounts/bridge-password
        public enum AccountsBridgePassword {}
        /// POST /api/accounts/bridge-password/clear
        public enum AccountsBridgePasswordClear {}
        /// POST /api/accounts/delete
        public enum AccountsDelete {}
        /// POST /api/accounts/edit
        public enum AccountsEdit {}
        /// POST /api/accounts/test
        public enum AccountsTest {}
        /// POST /api/accounts/trust-cert
        public enum AccountsTrustCert {}
        /// POST /api/ai/ask
        public enum AiAsk {}
        /// POST /api/ai/do-confirm
        public enum AiDoConfirm {}
        /// POST /api/ai/do-plan
        public enum AiDoPlan {}
        /// POST /api/ai/draft-reply
        public enum AiDraftReply {}
        /// POST /api/ai/extract
        public enum AiExtract {}
        /// POST /api/ai/key
        public enum AiKey {}
        /// POST /api/ai/key/clear
        public enum AiKeyClear {}
        /// POST /api/ai/rewrite
        public enum AiRewrite {}
        /// POST /api/ai/summarize
        public enum AiSummarize {}
        /// POST /api/archive
        public enum Archive {}
        /// POST /api/briefing
        public enum Briefing {}
        /// POST /api/briefing/refresh
        public enum BriefingRefresh {}
        /// POST /api/briefing/since
        public enum BriefingSince {}
        /// POST /api/bulk/file
        public enum BulkFile {}
        /// POST /api/bulk/move
        public enum BulkMove {}
        /// POST /api/calendar/event
        public enum CalendarEvent {}
        /// POST /api/cleanup/batch
        public enum CleanupBatch {}
        /// POST /api/cleanup/batch/preview
        public enum CleanupBatchPreview {}
        /// POST /api/cleanup/execute
        public enum CleanupExecute {}
        /// POST /api/cleanup/rationale
        public enum CleanupRationale {}
        /// POST /api/compose/preview
        public enum ComposePreview {}
        /// POST /api/contact/summary
        public enum ContactSummary {}
        /// POST /api/contacts/known
        public enum ContactsKnown {}
        /// POST /api/draft
        public enum Draft {}
        /// POST /api/draft/resume
        public enum DraftResume {}
        /// POST /api/filters
        public enum Filters {}
        /// POST /api/flag
        public enum Flag {}
        /// POST /api/folders/create
        public enum FoldersCreate {}
        /// POST /api/folders/delete
        public enum FoldersDelete {}
        /// POST /api/folders/rename
        public enum FoldersRename {}
        /// POST /api/followups/dismiss
        public enum FollowupsDismiss {}
        /// POST /api/invite/rsvp
        public enum InviteRsvp {}
        /// POST /api/junk
        public enum Junk {}
        /// POST /api/move
        public enum Move {}
        /// POST /api/mute
        public enum Mute {}
        /// POST /api/outbox/cancel
        public enum OutboxCancel {}
        /// POST /api/outbox/retry
        public enum OutboxRetry {}
        /// POST /api/outbox/sendnow
        public enum OutboxSendnow {}
        /// POST /api/pending/discard
        public enum PendingDiscard {}
        /// POST /api/pending/retry
        public enum PendingRetry {}
        /// POST /api/places
        public enum Places {}
        /// POST /api/preferences
        public enum Preferences {}
        /// POST /api/preferences/import
        public enum PreferencesImport {}
        /// POST /api/reconcile
        public enum Reconcile {}
        /// POST /api/refresh
        public enum Refresh {}
        /// POST /api/replylater
        public enum Replylater {}
        /// POST /api/review
        public enum Review {}
        /// POST /api/review/refresh
        public enum ReviewRefresh {}
        /// POST /api/rules
        public enum Rules {}
        /// POST /api/screener/suggest
        public enum ScreenerSuggest {}
        /// POST /api/seen
        public enum Seen {}
        /// POST /api/send
        public enum Send {}
        /// POST /api/snippets
        public enum Snippets {}
        /// POST /api/snooze
        public enum Snooze {}
        /// POST /api/sweep
        public enum Sweep {}
        /// POST /api/sync-retry-account
        public enum SyncRetryAccount {}
        /// POST /api/tasks
        public enum Tasks {}
        /// POST /api/tasks/done
        public enum TasksDone {}
        /// POST /api/tasks/remove
        public enum TasksRemove {}
        /// POST /api/thread/archive
        public enum ThreadArchive {}
        /// POST /api/thread/move
        public enum ThreadMove {}
        /// POST /api/thread/trash
        public enum ThreadTrash {}
        /// POST /api/tidy/undo
        public enum TidyUndo {}
        /// POST /api/trash
        public enum Trash {}
        /// POST /api/undo
        public enum Undo {}
        /// POST /api/unmute
        public enum Unmute {}
        /// POST /api/unsnooze
        public enum Unsnooze {}
        /// POST /api/views
        public enum Views {}
    }
}

extension API {
    /// The limits the page holds a person to, each the server's own (mailmind/http/page_limits.py): a request that
    /// would name more is sent as more requests, and a setting past one is refused before it is asked.
    public enum Limits {
        public static let aiBaseUrlChars = 500
        public static let aiDailyLimitUsd = 1000
        public static let aiModelChars = 200
        public static let attachBytes = 26214400
        public static let bulkRows = 500
        public static let instructionChars = 400
        public static let placeBytes = 4096
        public static let placesAtOnce = 64
        public static let snoozeCopies = 500
        public static let undoSendLongest = 60
        public static let undoSendShortest = 5
    }
}

extension API.Get.AccountHealth: GetRoute {
    public static let path = "/api/account-health"
    public typealias Answer = API.Standing
}

extension API.Get.Accounts: GetRoute {
    public static let path = "/api/accounts"
    public typealias Answer = API.Listed
}

extension API.Get.AccountsBridgePassword: GetRoute {
    public static let path = "/api/accounts/bridge-password"
    public typealias Answer = API.Kept
}

extension API.Get.AccountsPreset: GetRoute {
    public static let path = "/api/accounts/preset"
    public typealias Answer = API.PresetFound
}

extension API.Get.AiDraftAhead: GetRoute {
    public static let path = "/api/ai/draft-ahead"
    public typealias Answer = API.AiDraftAheadAnswer
}

extension API.Get.AiKey: GetRoute {
    public static let path = "/api/ai/key"
    public typealias Answer = API.KeysKept
}

extension API.Get.AiLocal: GetRoute {
    public static let path = "/api/ai/local"
    public typealias Answer = API.LocalServers
}

extension API.Get.AiModels: GetRoute {
    public static let path = "/api/ai/models"
    public typealias Answer = API.ModelList
}

extension API.Get.AiSpending: GetRoute {
    public static let path = "/api/ai/spending"
    public typealias Answer = API.Spending
}

extension API.Get.Audit: GetRoute {
    public static let path = "/api/audit"
    public typealias Answer = API.AuditTrail
}

extension API.Get.Briefing: GetRoute {
    public static let path = "/api/briefing"
    public typealias Answer = API.BriefingAnswer
}

extension API.Get.BriefingSince: GetRoute {
    public static let path = "/api/briefing/since"
    public typealias Answer = API.BriefingSinceAnswer
}

extension API.Get.CleanupSender: GetRoute {
    public static let path = "/api/cleanup/sender"
    public typealias Answer = API.SenderPreview
}

extension API.Get.CleanupSenders: GetRoute {
    public static let path = "/api/cleanup/senders"
    public typealias Answer = API.NoisySenders
}

extension API.Get.Contact: GetRoute {
    public static let path = "/api/contact"
    public typealias Answer = API.ContactCard
}

extension API.Get.Contacts: GetRoute {
    public static let path = "/api/contacts"
    public typealias Answer = API.Suggestions
}

extension API.Get.DebugAi: GetRoute {
    public static let path = "/api/debug/ai"
    public typealias Answer = API.AiCosts
}

extension API.Get.DebugHealth: GetRoute {
    public static let path = "/api/debug/health"
    public typealias Answer = API.AtAGlance
}

extension API.Get.DebugJobs: GetRoute {
    public static let path = "/api/debug/jobs"
    public typealias Answer = API.Jobs
}

extension API.Get.DebugPool: GetRoute {
    public static let path = "/api/debug/pool"
    public typealias Answer = API.PoolStats
}

extension API.Get.Digest: GetRoute {
    public static let path = "/api/digest"
    public typealias Answer = API.Digest
}

extension API.Get.Documents: GetRoute {
    public static let path = "/api/documents"
    public typealias Answer = API.Documents
}

extension API.Get.Events: GetRoute {
    public static let path = "/api/events"
    public typealias Answer = NotJSON
}

extension API.Get.Export: GetRoute {
    public static let path = "/api/export"
    public typealias Answer = NotJSON
}

extension API.Get.ExportArchive: GetRoute {
    public static let path = "/api/export/archive"
    public typealias Answer = NotJSON
}

extension API.Get.ExportPlan: GetRoute {
    public static let path = "/api/export/plan"
    public typealias Answer = API.ExportPlan
}

extension API.Get.Folders: GetRoute {
    public static let path = "/api/folders"
    public typealias Answer = API.Folders
}

extension API.Get.FoldersAcross: GetRoute {
    public static let path = "/api/folders/across"
    public typealias Answer = API.ViewCounts
}

extension API.Get.Inboxes: GetRoute {
    public static let path = "/api/inboxes"
    public typealias Answer = API.InboxesListing
}

extension API.Get.Labels: GetRoute {
    public static let path = "/api/labels"
    public typealias Answer = API.Labels
}

extension API.Get.MessageAttachment: GetRoute {
    public static let path = "/api/message/attachment"
    public typealias Answer = NotJSON
}

extension API.Get.MessageAttachmentPreviewHtml: GetRoute {
    public static let path = "/api/message/attachment/preview.html"
    public typealias Answer = NotJSON
}

extension API.Get.MessageBody: GetRoute {
    public static let path = "/api/message/body"
    public typealias Answer = API.MessageBodyAnswer
}

extension API.Get.MessageBodyHtml: GetRoute {
    public static let path = "/api/message/body.html"
    public typealias Answer = NotJSON
}

extension API.Get.MessageRow: GetRoute {
    public static let path = "/api/message/row"
    public typealias Answer = API.ListedCopy
}

extension API.Get.Messages: GetRoute {
    public static let path = "/api/messages"
    public typealias Answer = API.MessagesAnswer
}

extension API.Get.Outbox: GetRoute {
    public static let path = "/api/outbox"
    public typealias Answer = API.Outbox
}

extension API.Get.Pending: GetRoute {
    public static let path = "/api/pending"
    public typealias Answer = API.Pending
}

extension API.Get.Preferences: GetRoute {
    public static let path = "/api/preferences"
    public typealias Answer = API.PreferencesAnswer
}

extension API.Get.Review: GetRoute {
    public static let path = "/api/review"
    public typealias Answer = API.ReviewAnswer
}

extension API.Get.Rules: GetRoute {
    public static let path = "/api/rules"
    public typealias Answer = API.RuleBook
}

extension API.Get.Screener: GetRoute {
    public static let path = "/api/screener"
    public typealias Answer = API.ScreenerQueue
}

extension API.Get.Search: GetRoute {
    public static let path = "/api/search"
    public typealias Answer = API.SearchAnswer
}

extension API.Get.Snippets: GetRoute {
    public static let path = "/api/snippets"
    public typealias Answer = API.Snippets
}

extension API.Get.Stats: GetRoute {
    public static let path = "/api/stats"
    public typealias Answer = API.Stats
}

extension API.Get.Suggest: GetRoute {
    public static let path = "/api/suggest"
    public typealias Answer = API.SearchSuggestions
}

extension API.Get.Tasks: GetRoute {
    public static let path = "/api/tasks"
    public typealias Answer = API.TaskList
}

extension API.Get.Thread: GetRoute {
    public static let path = "/api/thread"
    public typealias Answer = API.Thread
}

extension API.Get.Tidy: GetRoute {
    public static let path = "/api/tidy"
    public typealias Answer = API.TidyRun
}

extension API.Get.Upcoming: GetRoute {
    public static let path = "/api/upcoming"
    public typealias Answer = API.Upcoming
}

extension API.Get.Views: GetRoute {
    public static let path = "/api/views"
    public typealias Answer = API.Views
}

extension API.Get.WaitingOnOthers: GetRoute {
    public static let path = "/api/waiting-on-others"
    public typealias Answer = API.WaitingOnOthers
}

extension API.Post.AccountsAdd: PostRoute {
    public static let path = "/api/accounts/add"
    public typealias Answer = API.Saved
    public typealias Body = API.AccountBody
}

extension API.Post.AccountsBridgePassword: PostRoute {
    public static let path = "/api/accounts/bridge-password"
    public typealias Answer = API.Kept
    public typealias Body = API.PasswordBody
}

extension API.Post.AccountsBridgePasswordClear: PostRoute {
    public static let path = "/api/accounts/bridge-password/clear"
    public typealias Answer = API.Kept
    public typealias Body = API.NoBody
}

extension API.Post.AccountsDelete: PostRoute {
    public static let path = "/api/accounts/delete"
    public typealias Answer = API.Removed
    public typealias Body = API.RemoveBody
}

extension API.Post.AccountsEdit: PostRoute {
    public static let path = "/api/accounts/edit"
    public typealias Answer = API.Saved
    public typealias Body = API.AccountBody
}

extension API.Post.AccountsTest: PostRoute {
    public static let path = "/api/accounts/test"
    public typealias Answer = API.AccountsTestAnswer
    public typealias Body = API.TryBody
}

extension API.Post.AccountsTrustCert: PostRoute {
    public static let path = "/api/accounts/trust-cert"
    public typealias Answer = API.Trusted
    public typealias Body = API.NamesAnAccount
}

extension API.Post.AiAsk: PostRoute {
    public static let path = "/api/ai/ask"
    public typealias Answer = API.Answered
    public typealias Body = API.AskBody
}

extension API.Post.AiDoConfirm: PostRoute {
    public static let path = "/api/ai/do-confirm"
    public typealias Answer = API.PlanCarriedOut
    public typealias Body = API.ConfirmBody
}

extension API.Post.AiDoPlan: PostRoute {
    public static let path = "/api/ai/do-plan"
    public typealias Answer = API.AiDoPlanAnswer
    public typealias Body = API.PlanBody
}

extension API.Post.AiDraftReply: PostRoute {
    public static let path = "/api/ai/draft-reply"
    public typealias Answer = API.DraftedReply
    public typealias Body = API.DraftReplyBody
}

extension API.Post.AiExtract: PostRoute {
    public static let path = "/api/ai/extract"
    public typealias Answer = API.Extracted
    public typealias Body = API.NamesAMessage
}

extension API.Post.AiKey: PostRoute {
    public static let path = "/api/ai/key"
    public typealias Answer = API.KeysKept
    public typealias Body = API.KeyBody
}

extension API.Post.AiKeyClear: PostRoute {
    public static let path = "/api/ai/key/clear"
    public typealias Answer = API.KeysKept
    public typealias Body = API.ProviderBody
}

extension API.Post.AiRewrite: PostRoute {
    public static let path = "/api/ai/rewrite"
    public typealias Answer = API.Rewritten
    public typealias Body = API.RewriteBody
}

extension API.Post.AiSummarize: PostRoute {
    public static let path = "/api/ai/summarize"
    public typealias Answer = API.Summary
    public typealias Body = API.SummarizeBody
}

extension API.Post.Archive: PostRoute {
    public static let path = "/api/archive"
    public typealias Answer = API.Filed
    public typealias Body = API.MoveBody
}

extension API.Post.Briefing: PostRoute {
    public static let path = "/api/briefing"
    public typealias Answer = API.Brief
    public typealias Body = API.NoBody
}

extension API.Post.BriefingRefresh: PostRoute {
    public static let path = "/api/briefing/refresh"
    public typealias Answer = API.Brief
    public typealias Body = API.NoBody
}

extension API.Post.BriefingSince: PostRoute {
    public static let path = "/api/briefing/since"
    public typealias Answer = API.BriefingSincePostAnswer
    public typealias Body = API.DeltaBody
}

extension API.Post.BulkFile: PostRoute {
    public static let path = "/api/bulk/file"
    public typealias Answer = API.RowsFiled
    public typealias Body = API.BulkFileBody
}

extension API.Post.BulkMove: PostRoute {
    public static let path = "/api/bulk/move"
    public typealias Answer = API.RowsFiled
    public typealias Body = API.BulkMoveBody
}

extension API.Post.CalendarEvent: PostRoute {
    public static let path = "/api/calendar/event"
    public typealias Answer = API.EventFile
    public typealias Body = API.EventBody
}

extension API.Post.CleanupBatch: PostRoute {
    public static let path = "/api/cleanup/batch"
    public typealias Answer = API.BatchCleanedUp
    public typealias Body = API.BatchCleanupBody
}

extension API.Post.CleanupBatchPreview: PostRoute {
    public static let path = "/api/cleanup/batch/preview"
    public typealias Answer = API.BatchPreview
    public typealias Body = API.BatchSendersBody
}

extension API.Post.CleanupExecute: PostRoute {
    public static let path = "/api/cleanup/execute"
    public typealias Answer = API.CleanedUp
    public typealias Body = API.CleanupBody
}

extension API.Post.CleanupRationale: PostRoute {
    public static let path = "/api/cleanup/rationale"
    public typealias Answer = API.CleanupRationales
    public typealias Body = API.RationaleBody
}

extension API.Post.ComposePreview: PostRoute {
    public static let path = "/api/compose/preview"
    public typealias Answer = API.Preview
    public typealias Body = API.PreviewBody
}

extension API.Post.ContactSummary: PostRoute {
    public static let path = "/api/contact/summary"
    public typealias Answer = API.SenderSummary
    public typealias Body = API.SenderSummaryBody
}

extension API.Post.ContactsKnown: PostRoute {
    public static let path = "/api/contacts/known"
    public typealias Answer = API.Known
    public typealias Body = API.KnownBody
}

extension API.Post.Draft: PostRoute {
    public static let path = "/api/draft"
    public typealias Answer = API.DraftAnswer
    public typealias Body = API.DraftBody
}

extension API.Post.DraftResume: PostRoute {
    public static let path = "/api/draft/resume"
    public typealias Answer = API.DraftOpened
    public typealias Body = API.ResumeBody
}

extension API.Post.Filters: PostRoute {
    public static let path = "/api/filters"
    public typealias Answer = API.Filters
    public typealias Body = API.FilterChange
}

extension API.Post.Flag: PostRoute {
    public static let path = "/api/flag"
    public typealias Answer = API.Filed
    public typealias Body = API.FlagBody
}

extension API.Post.FoldersCreate: PostRoute {
    public static let path = "/api/folders/create"
    public typealias Answer = API.FolderChanged
    public typealias Body = API.NewFolderBody
}

extension API.Post.FoldersDelete: PostRoute {
    public static let path = "/api/folders/delete"
    public typealias Answer = API.FolderDeleted
    public typealias Body = API.DeleteFolderBody
}

extension API.Post.FoldersRename: PostRoute {
    public static let path = "/api/folders/rename"
    public typealias Answer = API.FolderChanged
    public typealias Body = API.RenameFolderBody
}

extension API.Post.FollowupsDismiss: PostRoute {
    public static let path = "/api/followups/dismiss"
    public typealias Answer = API.OnThread
    public typealias Body = API.NamesAThread
}

extension API.Post.InviteRsvp: PostRoute {
    public static let path = "/api/invite/rsvp"
    public typealias Answer = API.RsvpQueued
    public typealias Body = API.RsvpBody
}

extension API.Post.Junk: PostRoute {
    public static let path = "/api/junk"
    public typealias Answer = API.Filed
    public typealias Body = API.MoveBody
}

extension API.Post.Move: PostRoute {
    public static let path = "/api/move"
    public typealias Answer = API.Filed
    public typealias Body = API.MoveToBody
}

extension API.Post.Mute: PostRoute {
    public static let path = "/api/mute"
    public typealias Answer = API.OnThread
    public typealias Body = API.MuteBody
}

extension API.Post.OutboxCancel: PostRoute {
    public static let path = "/api/outbox/cancel"
    public typealias Answer = API.Cancelled
    public typealias Body = API.NamesASend
}

extension API.Post.OutboxRetry: PostRoute {
    public static let path = "/api/outbox/retry"
    public typealias Answer = API.Retried
    public typealias Body = API.NamesASend
}

extension API.Post.OutboxSendnow: PostRoute {
    public static let path = "/api/outbox/sendnow"
    public typealias Answer = API.SentNow
    public typealias Body = API.NamesASend
}

extension API.Post.PendingDiscard: PostRoute {
    public static let path = "/api/pending/discard"
    public typealias Answer = API.Done
    public typealias Body = API.NamesAPending
}

extension API.Post.PendingRetry: PostRoute {
    public static let path = "/api/pending/retry"
    public typealias Answer = API.Done
    public typealias Body = API.NamesAPending
}

extension API.Post.Places: PostRoute {
    public static let path = "/api/places"
    public typealias Answer = API.PlacesKept
    public typealias Body = API.PlacesBody
}

extension API.Post.Preferences: PostRoute {
    public static let path = "/api/preferences"
    public typealias Answer = API.PreferencesAnswer
    public typealias Body = API.ChangedPreferences
}

extension API.Post.PreferencesImport: PostRoute {
    public static let path = "/api/preferences/import"
    public typealias Answer = API.PreferencesAnswer
    public typealias Body = API.HeldPreferences
}

extension API.Post.Reconcile: PostRoute {
    public static let path = "/api/reconcile"
    public typealias Answer = API.Reconciled
    public typealias Body = API.ReconcileBody
}

extension API.Post.Refresh: PostRoute {
    public static let path = "/api/refresh"
    public typealias Answer = API.SyncStarted
    public typealias Body = API.NoBody
}

extension API.Post.Replylater: PostRoute {
    public static let path = "/api/replylater"
    public typealias Answer = API.RepliedLater
    public typealias Body = API.ReplyLaterBody
}

extension API.Post.Review: PostRoute {
    public static let path = "/api/review"
    public typealias Answer = API.Review
    public typealias Body = API.NoBody
}

extension API.Post.ReviewRefresh: PostRoute {
    public static let path = "/api/review/refresh"
    public typealias Answer = API.Review
    public typealias Body = API.NoBody
}

extension API.Post.Rules: PostRoute {
    public static let path = "/api/rules"
    public typealias Answer = API.RuleChanged
    public typealias Body = API.RuleBody
}

extension API.Post.ScreenerSuggest: PostRoute {
    public static let path = "/api/screener/suggest"
    public typealias Answer = API.ScreenerSuggestions
    public typealias Body = API.ScreenerSuggestBody
}

extension API.Post.Seen: PostRoute {
    public static let path = "/api/seen"
    public typealias Answer = API.Filed
    public typealias Body = API.SeenBody
}

extension API.Post.Send: PostRoute {
    public static let path = "/api/send"
    public typealias Answer = API.Queued
    public typealias Body = API.SendBody
}

extension API.Post.Snippets: PostRoute {
    public static let path = "/api/snippets"
    public typealias Answer = API.Snippets
    public typealias Body = API.SnippetChange
}

extension API.Post.Snooze: PostRoute {
    public static let path = "/api/snooze"
    public typealias Answer = API.Snoozed
    public typealias Body = API.SnoozeBody
}

extension API.Post.Sweep: PostRoute {
    public static let path = "/api/sweep"
    public typealias Answer = API.Swept
    public typealias Body = API.SweepBody
}

extension API.Post.SyncRetryAccount: PostRoute {
    public static let path = "/api/sync-retry-account"
    public typealias Answer = API.SyncRetried
    public typealias Body = API.SyncRetryBody
}

extension API.Post.Tasks: PostRoute {
    public static let path = "/api/tasks"
    public typealias Answer = API.TaskAdded
    public typealias Body = API.TaskBody
}

extension API.Post.TasksDone: PostRoute {
    public static let path = "/api/tasks/done"
    public typealias Answer = API.TaskGone
    public typealias Body = API.TaskDoneBody
}

extension API.Post.TasksRemove: PostRoute {
    public static let path = "/api/tasks/remove"
    public typealias Answer = API.TaskGone
    public typealias Body = API.TaskRemoveBody
}

extension API.Post.ThreadArchive: PostRoute {
    public static let path = "/api/thread/archive"
    public typealias Answer = API.ManyFiled
    public typealias Body = API.MoveBody
}

extension API.Post.ThreadMove: PostRoute {
    public static let path = "/api/thread/move"
    public typealias Answer = API.ManyFiled
    public typealias Body = API.MoveToBody
}

extension API.Post.ThreadTrash: PostRoute {
    public static let path = "/api/thread/trash"
    public typealias Answer = API.ManyFiled
    public typealias Body = API.MoveBody
}

extension API.Post.TidyUndo: PostRoute {
    public static let path = "/api/tidy/undo"
    public typealias Answer = API.Undone
    public typealias Body = API.TidyUndoBody
}

extension API.Post.Trash: PostRoute {
    public static let path = "/api/trash"
    public typealias Answer = API.Filed
    public typealias Body = API.MoveBody
}

extension API.Post.Undo: PostRoute {
    public static let path = "/api/undo"
    public typealias Answer = API.Undone
    public typealias Body = API.NoBody
}

extension API.Post.Unmute: PostRoute {
    public static let path = "/api/unmute"
    public typealias Answer = API.OnThread
    public typealias Body = API.NamesAThread
}

extension API.Post.Unsnooze: PostRoute {
    public static let path = "/api/unsnooze"
    public typealias Answer = API.Unsnoozed
    public typealias Body = API.NamesThreadOrMessages
}

extension API.Post.Views: PostRoute {
    public static let path = "/api/views"
    public typealias Answer = API.Views
    public typealias Body = API.ViewChange
}
