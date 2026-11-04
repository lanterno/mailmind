-- index idx_att_msg
CREATE INDEX idx_att_msg ON attachments(message_pk);

-- index idx_draft_ahead_created
CREATE INDEX idx_draft_ahead_created ON draft_ahead(created_ts);

-- index idx_followups_state
CREATE INDEX idx_followups_state ON followups(account, state);

-- index idx_msg_acct_folder
CREATE INDEX idx_msg_acct_folder ON messages(account, folder, ts);

-- index idx_msg_acct_folder_thread
CREATE INDEX idx_msg_acct_folder_thread ON messages(account, folder, thread_id);

-- index idx_msg_junk
CREATE INDEX idx_msg_junk ON messages(is_junk);

-- index idx_msg_snooze
CREATE INDEX idx_msg_snooze ON messages(snoozed_until) WHERE snoozed_until IS NOT NULL;

-- index idx_msg_thread
CREATE INDEX idx_msg_thread ON messages(thread_id);

-- index idx_msg_ts
CREATE INDEX idx_msg_ts ON messages(ts);

-- index idx_muted_acct
CREATE INDEX idx_muted_acct ON muted(account);

-- index idx_outbox_created
CREATE INDEX idx_outbox_created ON outbox(created_at);

-- index idx_outbox_state
CREATE INDEX idx_outbox_state ON outbox(state, next_attempt_at);

-- index idx_readmut_state
CREATE INDEX idx_readmut_state ON read_mutations(state, next_attempt_at);

-- index idx_unsub_log_addr
CREATE INDEX idx_unsub_log_addr ON unsub_log(from_addr);

-- table ai_usage
CREATE TABLE ai_usage( day TEXT NOT NULL, feature TEXT NOT NULL, model TEXT NOT NULL, served TEXT NOT NULL DEFAULT '', outcome TEXT NOT NULL, calls INTEGER NOT NULL DEFAULT 0, input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0, cache_write_tokens INTEGER NOT NULL DEFAULT 0, cache_read_tokens INTEGER NOT NULL DEFAULT 0, cost_micros INTEGER NOT NULL DEFAULT 0, last_ts INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (day, feature, model, served, outcome));

-- table attachments
CREATE TABLE attachments ( id INTEGER PRIMARY KEY AUTOINCREMENT, message_pk INTEGER REFERENCES messages(pk) ON DELETE CASCADE, part_id TEXT, filename TEXT, content_type TEXT, size INTEGER, sha256 TEXT, -- content-addressed file under attachments/ is_inline INTEGER DEFAULT 0, content_id TEXT );

-- table bodies
CREATE TABLE bodies ( message_pk INTEGER PRIMARY KEY REFERENCES messages(pk) ON DELETE CASCADE, raw BLOB, -- raw RFC822 bytes (lossless; parse on read) fetched_at TEXT );

-- table briefing_cache
CREATE TABLE briefing_cache ( -- AI "Today" card, keyed on (sorted-accounts|local-date) key TEXT PRIMARY KEY, fingerprint TEXT, narrative TEXT, groups_json TEXT, model TEXT, created_at TEXT );

-- table draft_ahead
CREATE TABLE draft_ahead ( -- durable AI pre-draft cache; key (account,ref_key) NEVER pk account TEXT NOT NULL, ref_key TEXT NOT NULL, -- 't:<thread_id>' or 'm:<message_id>' fp TEXT NOT NULL DEFAULT '', -- thread-state hash captured BEFORE generation body TEXT, -- NULL => failure-only row, never servable tone TEXT, model TEXT, source TEXT NOT NULL DEFAULT 'dwell', -- 'dwell' | 'topk' | 'demand' src_pk INTEGER, -- advisory only: which message the draft answers src_message_id TEXT, created_ts INTEGER NOT NULL DEFAULT 0, last_hit_ts INTEGER, fail_ts INTEGER NOT NULL DEFAULT 0, fail_count INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(account, ref_key) );

-- table drafts
CREATE TABLE drafts ( id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL, folder TEXT, uid INTEGER, message_id TEXT, uidvalidity INTEGER, in_reply_to_pk INTEGER, mode TEXT, subject TEXT, created_at TEXT, updated_at TEXT , own_text TEXT, tail_text TEXT, tail_html TEXT, bcc TEXT, raw_sha TEXT);

-- table followups
CREATE TABLE followups ( -- commitment tracking; keyed on (account,thread_id), NEVER pk account TEXT NOT NULL, thread_id TEXT NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending', anchor_pk INTEGER, first_sent_ts INTEGER, last_msg_ts INTEGER, due_ts INTEGER, created_at TEXT, resolved_at TEXT, PRIMARY KEY(account, thread_id) );

-- table messages
CREATE TABLE messages ( pk INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL, kind TEXT NOT NULL, -- gmail | proton | imap folder TEXT NOT NULL, uid INTEGER NOT NULL, message_id TEXT, -- RFC822 Message-ID gmail_thread TEXT, -- X-GM-THRID (gmail) for threading thread_id TEXT, -- cross-provider computed thread id in_reply_to TEXT, refs TEXT, -- In-Reply-To / References for threading from_addr TEXT, from_name TEXT, subject TEXT, date_iso TEXT, ts INTEGER, snippet TEXT, -- plain-text body, truncated (list + AI) bucket TEXT, confidence TEXT, reason TEXT, is_junk INTEGER DEFAULT 0, -- a BADGE now (Mailbox shows junk too) verified INTEGER DEFAULT 0, last_seen TEXT, seen INTEGER DEFAULT 0, -- \Seen flagged INTEGER DEFAULT 0, -- \Flagged (star) answered INTEGER DEFAULT 0, -- \Answered draft INTEGER DEFAULT 0, -- \Draft deleted INTEGER DEFAULT 0, -- \Deleted has_attach INTEGER DEFAULT 0, body_fetched INTEGER DEFAULT 0, -- 1 once raw RFC822 is in bodies body_text TEXT, -- plain-text body for FTS (NULL until fetched) snoozed_until INTEGER, recipients TEXT, list_unsub_raw TEXT, list_unsub_method TEXT, -- unix secs; NULL = not snoozed (hidden from lists until due) UNIQUE(account, folder, uid) );

-- table msg_fts
CREATE VIRTUAL TABLE msg_fts USING fts5(subject, from_name, from_addr, recipients, snippet, body_text, tokenize='unicode61 remove_diacritics 2');

-- table msg_fts_config
CREATE TABLE 'msg_fts_config'(k PRIMARY KEY, v) WITHOUT ROWID;

-- table msg_fts_content
CREATE TABLE 'msg_fts_content'(id INTEGER PRIMARY KEY, c0, c1, c2, c3, c4, c5);

-- table msg_fts_data
CREATE TABLE 'msg_fts_data'(id INTEGER PRIMARY KEY, block BLOB);

-- table msg_fts_docsize
CREATE TABLE 'msg_fts_docsize'(id INTEGER PRIMARY KEY, sz BLOB);

-- table msg_fts_idx
CREATE TABLE 'msg_fts_idx'(segid, term, pgno, PRIMARY KEY(segid, term)) WITHOUT ROWID;

-- table muted
CREATE TABLE muted ( -- silenced threads; keyed on (account,thread_id), NEVER pk account TEXT NOT NULL, thread_id TEXT NOT NULL, muted_at TEXT, PRIMARY KEY(account, thread_id) );

-- table outbox
CREATE TABLE outbox ( id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL, -- the FROM account email (must exist in config) idempotency_key TEXT NOT NULL UNIQUE, -- one logical send per key (dedupes double-submit) message_id TEXT NOT NULL, -- minted at build; stable across retries in_reply_to_pk INTEGER, -- original pk (reply) -> set \Answered after send envelope TEXT NOT NULL, -- JSON [addr-spec,...] full delivery set incl. Bcc (PRIVATE; never in raw) recipients TEXT NOT NULL, -- JSON {to,cc,bcc} for the outbox UI only subject TEXT, raw BLOB NOT NULL, -- Bcc-LESS RFC822 bytes: sent on the wire AND APPENDed to Sent state TEXT NOT NULL DEFAULT 'queued', -- queued | sending | sent | error attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at INTEGER NOT NULL DEFAULT 0, -- unix secs; backoff gate delivered INTEGER NOT NULL DEFAULT 0, -- 1 once the wire send succeeded (never re-send) sent_appended INTEGER NOT NULL DEFAULT 0, -- done-once: Sent copy made answered_set INTEGER NOT NULL DEFAULT 0, -- done-once: \Answered set on original followup_recorded INTEGER NOT NULL DEFAULT 0, -- done-once: sent-no-reply followup seeded last_error TEXT, -- SCRUBBED code only draft_id INTEGER, -- supersede this draft after delivery draft_deleted INTEGER NOT NULL DEFAULT 0, -- done-once: draft removed post-send created_at TEXT NOT NULL, sent_at TEXT );

-- table read_mutations
CREATE TABLE read_mutations ( -- offline-safe read-side mutation queue (mirrors outbox); keyed on message_id, NEVER pk id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE, -- one logical mutation per key (dedupes double-submit) op TEXT NOT NULL, -- trash | archive | junk | move | flag message_id TEXT, -- RFC822 Message-ID: the DURABLE key (survives reconcile re-pk) src_folder TEXT NOT NULL, -- folder the message lived in at enqueue dest TEXT, -- move/junk/archive target (NULL for flag / gmail label-remove) payload TEXT NOT NULL, -- JSON {pk,uid,kind,flags?,is_gmail,undo_label,subject,thread_id} state TEXT NOT NULL DEFAULT 'queued', -- queued | running | done | error attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at INTEGER NOT NULL DEFAULT 0, applied INTEGER NOT NULL DEFAULT 0, -- EXACTLY-ONCE flag (mirrors outbox.delivered): set the instant the IMAP op lands last_error TEXT, -- SCRUBBED code only created_at TEXT NOT NULL, done_at TEXT );

-- table summaries
CREATE TABLE summaries ( message_pk INTEGER PRIMARY KEY REFERENCES messages(pk) ON DELETE CASCADE, summary TEXT, importance INTEGER, category TEXT, action TEXT, model TEXT, created_at TEXT );

-- table sync_state
CREATE TABLE sync_state ( account TEXT NOT NULL, folder TEXT NOT NULL DEFAULT 'INBOX', last_sync TEXT, last_uid INTEGER DEFAULT 0, uidvalidity INTEGER, last_ok_ts TEXT, -- health: last successful sync (added _m15) last_error TEXT, -- exception CLASS name only (scrubbed; never the message) last_error_ts TEXT, last_error_kind TEXT, -- coarse enum: TIMEOUT|AUTH|UIDVALIDITY|IMAP|NETWORK|UNKNOWN sync_count INTEGER DEFAULT 0, PRIMARY KEY(account, folder) );

-- table thread_summaries
CREATE TABLE thread_summaries ( account TEXT NOT NULL, thread_id TEXT NOT NULL, scope TEXT NOT NULL DEFAULT 'thread', -- thread | message fingerprint TEXT NOT NULL, -- content hash; mismatch => stale tldr TEXT, points TEXT, action TEXT, participants TEXT, -- points/participants = JSON message_count INTEGER, model TEXT, created_at TEXT, PRIMARY KEY(account, thread_id, scope) );

-- table unsub_log
CREATE TABLE unsub_log( id INTEGER PRIMARY KEY, from_addr TEXT, method TEXT, status TEXT, http_status INTEGER, ts TEXT, idempotency_key TEXT UNIQUE);

PRAGMA user_version=21;
