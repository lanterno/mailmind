import Foundation

/// What the routes answer, as the status and Settings tests hand it to them: fictional accounts at example.test, and
/// the server's own default preferences.
enum StatusFixtures {
    static func decode<T: Decodable>(_ type: T.Type, _ json: String) -> T {
        do {
            return try JSONDecoder().decode(T.self, from: Data(json.utf8))
        } catch {
            fatalError("a fixture didn't read as \(T.self): \(error)")
        }
    }

    static func digest(accounts: [String] = ["ada@example.test", "news@example.test"],
                       aliases: [String: String] = ["ada@example.test": "Ada"],
                       status: [String: String] = [:], last: String? = nil, running: Bool = false,
                       profile: String = "full", engine: String = "api", keyFrom: String = "mailmind.config.json",
                       spent: Int? = 120_000, lastOk: Bool? = true, lastError: String? = nil,
                       total: Int = 120, provider: String = "anthropic", off: String? = nil,
                       model: String = "Claude Haiku 4.5", backgroundModel: String? = nil,
                       waiting: Int? = nil) -> API.Digest
    {
        let aliasJSON = aliases.map { "\"\($0.key)\": \"\($0.value)\"" }.joined(separator: ", ")
        let statusJSON = status.map { "\"\($0.key)\": {\"health\": \"\($0.value)\", \"msg\": \"\"}" }
            .joined(separator: ", ")
        let accountsJSON = accounts.map { "\"\($0)\"" }.joined(separator: ", ")
        let lastJSON = last.map { "\"\($0)\"" } ?? "null"
        let spentJSON = spent.map(String.init) ?? "null"
        let lastOkJSON = lastOk.map { $0 ? "true" : "false" } ?? "null"
        let lastErrorJSON = lastError.map { "\"\($0)\"" } ?? "null"
        // Off with no reason named is off for want of a key, as a fresh home is.
        let offJSON = (off ?? (engine == "none" ? "no-key" : nil)).map { "\"\($0)\"" } ?? "null"
        // Background summaries ask the model chosen unless another is named, as for a model on this Mac.
        let background = engine == "none" ? "" : backgroundModel ?? model
        let waitingJSON = waiting.map(String.init) ?? "null"
        return decode(API.Digest.self, """
            {"accounts": [\(accountsJSON)],
             "ai": {"engine": "\(engine)", "key_from": "\(keyFrom)", "last_ts": 0, "limit_micros": 15000000,
                    "model": "\(engine == "none" ? "" : model)", "spent_today_micros": \(spentJSON),
                    "last_ok": \(lastOkJSON), "last_error": \(lastErrorJSON), "provider": "\(provider)",
                    "off": \(offJSON), "background_model": "\(background)", "cloud_model": false,
                    "summaries_waiting": \(waitingJSON)},
             "aliases": {\(aliasJSON)}, "colours": {}, "generated": "2026-11-14T09:00:00Z", "items": [], "profile": "\(profile)",
             "send_accounts": [\(accountsJSON)], "signature_default": "", "signatures": {},
             "stats": {"kept": 100, "needs": 3, "summarized": 42, "total": \(total), "verified": 0},
             "status": {\(statusJSON)}, "sync": {"last": \(lastJSON), "result": "idle", "running": \(running)}}
            """)
    }

    static func account(_ email: String, alias: String? = nil, type: String = "imap", style: String = "keychain",
                        set: Bool = true, signature: String = "", ref: String? = nil, variable: String? = nil,
                        host: String = "imap.example.test", smtpHost: String = "smtp.example.test")
        -> API.AccountRow
    {
        let aliasJSON = alias.map { "\"\($0)\"" } ?? "null"
        let refJSON = ref.map { "\"\($0)\"" } ?? "null"
        let varJSON = variable.map { "\"\($0)\"" } ?? "null"
        return decode(API.AccountRow.self, """
            {"alias": \(aliasJSON), "credential": {"set": \(set), "style": "\(style)", "ref": \(refJSON), "var": \(varJSON)},
             "defaults_applied": {"host": false, "port": false, "smtp_host": false, "smtp_port": false,
                                  "smtp_starttls": false, "starttls": false, "trash": false},
             "disabled": false, "email": "\(email)", "host": "\(host)", "port": 993,
             "signature": "\(signature)", "smtp_host": "\(smtpHost)", "smtp_port": 465,
             "smtp_starttls": false, "starttls": false, "trash": "Trash", "type": "\(type)"}
            """)
    }

    static func standing(_ account: String, ok: String? = nil, error: String? = nil, kind: String? = nil,
                         errorAt: String? = nil, count: Int = 0) -> API.AccountStanding
    {
        func text(_ value: String?) -> String { value.map { "\"\($0)\"" } ?? "null" }
        let healthy = ok != nil && error == nil
        return decode(API.AccountStanding.self, """
            {"account": "\(account)", "healthy": \(healthy), "last_error": \(text(error)),
             "last_error_kind": \(text(kind)), "last_error_ts": \(text(errorAt)), "last_ok_ts": \(text(ok)),
             "live": "", "sync_count": \(count)}
            """)
    }

    static func preferences(quiet: Bool = false, from: String = "22:00", until: String = "07:00",
                            newMail: Bool = true, meetings: Bool = true, guideOffered: Bool = false,
                            aiEnabled: Bool = true, provider: String = "anthropic", model: String = "",
                            baseURL: String = "", draftAhead: Bool = false) -> API.Preferences
    {
        decode(API.Preferences.self, """
            {"ai_base_url": "\(baseURL)", "ai_enabled": \(aiEnabled), "ai_model": "\(model)",
             "ai_provider": "\(provider)",
             "ai_daily_limit_usd": 15.0, "auto_tidy": false, "conversations": true, "density": "comfortable",
             "draft_ahead": \(draftAhead),
             "draft_tone": "balanced", "guide_offered": \(guideOffered), "high_contrast": false, "mail_colors": "auto",
             "morning_brief": false, "notify_meetings": \(meetings), "notify_new_mail": \(newMail),
             "quiet_from": "\(from)", "quiet_hours": \(quiet), "quiet_until": "\(until)", "read_font": "sans",
             "read_size": "m", "read_width": "medium", "search_scope": "all", "single_keys": true, "theme": "day",
             "undo_send_seconds": 10, "weekly_review": false}
            """)
    }

    static func meeting(_ summary: String, start: Date, allDay: Bool = false, key: Int = 7) -> API.UpcomingEvent {
        let iso = ISO8601DateFormatter().string(from: start)
        let end = ISO8601DateFormatter().string(from: start.addingTimeInterval(1800))
        return decode(API.UpcomingEvent.self, """
            {"account": "ada@example.test", "all_day": \(allDay), "end": "\(end)", "folder": "INBOX", "invite": true,
             "key": \(key), "location": "Room 4", "organizer": "grace@example.test", "recurrence": "",
             "start": "\(iso)", "summary": "\(summary)", "thread_id": null, "uid": \(key)}
            """)
    }

    /// A calendar and a locale that don't depend on the Mac the tests run on.
    static var utc: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        calendar.locale = Locale(identifier: "en_US")
        return calendar
    }

    static func at(_ iso: String) -> Date {
        ISO8601DateFormatter().date(from: iso)!
    }
}
