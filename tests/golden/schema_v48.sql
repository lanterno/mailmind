-- index idx_ai_batch_items_batch
CREATE INDEX idx_ai_batch_items_batch ON ai_batch_items(batch_id);

-- index idx_att_msg
CREATE INDEX idx_att_msg ON attachments(message_pk);

-- index idx_commands_message
CREATE INDEX idx_commands_message ON mail_commands(account, message_id);

-- index idx_commands_state
CREATE INDEX idx_commands_state ON mail_commands(state, next_attempt_at);

-- index idx_draft_ahead_created
CREATE INDEX idx_draft_ahead_created ON draft_ahead(created_ts);

-- index idx_draft_journal_due
CREATE INDEX idx_draft_journal_due ON draft_journal(due_at);

-- index idx_followups_state
CREATE INDEX idx_followups_state ON followups(account, state);

-- index idx_message_recipients_addr
CREATE INDEX idx_message_recipients_addr ON message_recipients(addr);

-- index idx_messages_key
CREATE INDEX idx_messages_key ON messages(message_key);

-- index idx_msg_acct_folder
CREATE INDEX idx_msg_acct_folder ON messages(account, folder, ts);

-- index idx_msg_acct_folder_thread
CREATE INDEX idx_msg_acct_folder_thread ON messages(account, folder, thread_id);

-- index idx_msg_junk
CREATE INDEX idx_msg_junk ON messages(is_junk);

-- index idx_msg_thread
CREATE INDEX idx_msg_thread ON messages(thread_id);

-- index idx_msg_ts
CREATE INDEX idx_msg_ts ON messages(ts);

-- index idx_muted_acct
CREATE INDEX idx_muted_acct ON muted(account);

-- index idx_outbox_created
CREATE INDEX idx_outbox_created ON outbox(created_at);

-- index idx_outbox_orig_thread
CREATE INDEX idx_outbox_orig_thread ON outbox(orig_account, orig_thread_id) WHERE orig_thread_id IS NOT NULL;

-- index idx_outbox_state
CREATE INDEX idx_outbox_state ON outbox(state, next_attempt_at);

-- index idx_thread_links_message
CREATE INDEX idx_thread_links_message ON thread_links(message_pk);

-- index idx_unsub_log_addr
CREATE INDEX idx_unsub_log_addr ON unsub_log(from_addr);

-- table ai_batch_items
CREATE TABLE ai_batch_items( message_key INTEGER PRIMARY KEY REFERENCES message_identity(key) ON DELETE CASCADE, batch_id INTEGER NOT NULL REFERENCES ai_batches(id) ON DELETE CASCADE);

-- table ai_batches
CREATE TABLE ai_batches( id INTEGER PRIMARY KEY AUTOINCREMENT, remote_id TEXT, feature TEXT NOT NULL, model TEXT NOT NULL, state TEXT NOT NULL, requests INTEGER NOT NULL, estimate_micros INTEGER NOT NULL, cutoff_ts INTEGER NOT NULL, created_at INTEGER NOT NULL, polled_at INTEGER NOT NULL DEFAULT 0, ended_at INTEGER, prompt_version INTEGER);

-- table ai_usage
CREATE TABLE ai_usage( day TEXT NOT NULL, feature TEXT NOT NULL, model TEXT NOT NULL, served TEXT NOT NULL DEFAULT '', outcome TEXT NOT NULL, calls INTEGER NOT NULL DEFAULT 0, input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0, cache_write_tokens INTEGER NOT NULL DEFAULT 0, cache_read_tokens INTEGER NOT NULL DEFAULT 0, cost_micros INTEGER NOT NULL DEFAULT 0, last_ts INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (day, feature, model, served, outcome));

-- table assessments
CREATE TABLE assessments( message_key INTEGER PRIMARY KEY REFERENCES message_identity(key) ON DELETE CASCADE, summary TEXT, importance INTEGER, category TEXT, action TEXT, model TEXT, created_at TEXT, served_by TEXT, prompt_version INTEGER, outcome TEXT);

-- table attachments
CREATE TABLE attachments ( id INTEGER PRIMARY KEY AUTOINCREMENT, message_pk INTEGER REFERENCES messages(pk) ON DELETE CASCADE, part_id TEXT, filename TEXT, content_type TEXT, size INTEGER, sha256 TEXT, -- content-addressed file under attachments/ is_inline INTEGER DEFAULT 0, content_id TEXT );

-- table bodies
CREATE TABLE bodies ( message_pk INTEGER PRIMARY KEY REFERENCES messages(pk) ON DELETE CASCADE, raw BLOB, -- raw RFC822 bytes (lossless; parse on read) fetched_at TEXT );

-- table briefs
CREATE TABLE briefs( accounts TEXT NOT NULL, day TEXT NOT NULL, fingerprint TEXT NOT NULL, narrative TEXT NOT NULL, groups_json TEXT NOT NULL, model TEXT, created_at TEXT NOT NULL, PRIMARY KEY(accounts, day));

-- table deltas
CREATE TABLE deltas( accounts TEXT NOT NULL, since_ts INTEGER NOT NULL, fingerprint TEXT NOT NULL, headline TEXT NOT NULL, items_json TEXT NOT NULL, model TEXT, created_at TEXT NOT NULL, PRIMARY KEY(accounts, since_ts, fingerprint));

-- table draft_ahead
CREATE TABLE draft_ahead ( -- durable AI pre-draft cache; key (account,ref_key) NEVER pk account TEXT NOT NULL, ref_key TEXT NOT NULL, -- 't:<thread_id>' or 'm:<message_id>' fp TEXT NOT NULL DEFAULT '', -- thread-state hash captured BEFORE generation body TEXT, -- NULL => failure-only row, never servable tone TEXT, model TEXT, source TEXT NOT NULL DEFAULT 'dwell', -- 'dwell' | 'topk' | 'demand' src_message_id TEXT, created_ts INTEGER NOT NULL DEFAULT 0, last_hit_ts INTEGER, fail_ts INTEGER NOT NULL DEFAULT 0, fail_count INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(account, ref_key) );

-- table draft_journal
CREATE TABLE draft_journal( draft_id INTEGER PRIMARY KEY REFERENCES drafts(id) ON DELETE CASCADE, version INTEGER NOT NULL, account TEXT NOT NULL, -- where the copy goes: From may have changed since the last discard INTEGER NOT NULL DEFAULT 0, -- 1: all that's left is to remove the copy message_id TEXT, raw BLOB, raw_sha TEXT, own_text TEXT, tail_text TEXT, tail_html TEXT, bcc TEXT, tried TEXT, -- the Message-ID of a copy a try may have left saved_at INTEGER NOT NULL, due_at INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0 );

-- table drafts
CREATE TABLE drafts ( id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL, folder TEXT, uid INTEGER, message_id TEXT, uidvalidity INTEGER, mode TEXT, subject TEXT, created_at TEXT, updated_at TEXT , own_text TEXT, tail_text TEXT, tail_html TEXT, bcc TEXT, raw_sha TEXT, idempotency_key TEXT, version INTEGER NOT NULL DEFAULT 0, saved_by TEXT, pushed_at INTEGER);

-- table followups
CREATE TABLE followups ( -- commitment tracking; keyed on (account,thread_id), NEVER pk account TEXT NOT NULL, thread_id TEXT NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending', first_sent_ts INTEGER, last_msg_ts INTEGER, due_ts INTEGER, created_at TEXT, resolved_at TEXT, PRIMARY KEY(account, thread_id) );

-- table mail_commands
CREATE TABLE mail_commands( -- keyed on message_id, never pk id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE, -- one per request, so a resubmit dedupes kind TEXT NOT NULL, -- trash | archive | junk | move | flag message_id TEXT, -- RFC822 Message-ID: what outlasts a renumber src_folder TEXT NOT NULL, -- where the message was when it was accepted dest TEXT, -- where it goes, once that is known params TEXT NOT NULL, -- JSON: the copy as accepted, flags, what they replaced state TEXT NOT NULL DEFAULT 'queued', -- queued | running | done | error attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at INTEGER NOT NULL DEFAULT 0, applied INTEGER NOT NULL DEFAULT 0, -- done once: the server holds the change begun INTEGER NOT NULL DEFAULT 0, -- a try may have reached the server: ask before repeating outcome TEXT, -- JSON: what a reversal needs from the server's answer last_error TEXT, -- a fixed code or a class name, nothing more created_at TEXT NOT NULL, done_at TEXT);

-- table message_identity
CREATE TABLE message_identity( key INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL, identity TEXT NOT NULL, gone_at INTEGER, UNIQUE(account, identity));

-- table message_recipients
CREATE TABLE message_recipients( message_pk INTEGER NOT NULL REFERENCES messages(pk) ON DELETE CASCADE, addr TEXT NOT NULL, PRIMARY KEY(message_pk, addr));

-- table messages
CREATE TABLE messages ( pk INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL, kind TEXT NOT NULL, -- gmail | proton | imap folder TEXT NOT NULL, uid INTEGER NOT NULL, message_id TEXT, -- RFC822 Message-ID gmail_thread TEXT, -- X-GM-THRID (gmail) for threading thread_id TEXT, -- cross-provider computed thread id in_reply_to TEXT, refs TEXT, -- In-Reply-To / References for threading from_addr TEXT, from_name TEXT, subject TEXT, date_iso TEXT, ts INTEGER, snippet TEXT, -- plain-text body, truncated (list + AI) bucket TEXT, confidence TEXT, reason TEXT, is_junk INTEGER DEFAULT 0, -- a BADGE now (Mailbox shows junk too) verified INTEGER DEFAULT 0, last_seen TEXT, seen INTEGER DEFAULT 0, -- \Seen flagged INTEGER DEFAULT 0, -- \Flagged (star) answered INTEGER DEFAULT 0, -- \Answered draft INTEGER DEFAULT 0, -- \Draft deleted INTEGER DEFAULT 0, -- \Deleted has_attach INTEGER DEFAULT 0, body_fetched INTEGER DEFAULT 0, -- 1 once raw RFC822 is in bodies body_text TEXT, -- plain-text body for FTS (NULL until fetched) recipients TEXT, list_unsub_raw TEXT, list_unsub_method TEXT, pending_removal INTEGER, message_key INTEGER, -- unix secs; NULL = not snoozed (hidden from lists until due) UNIQUE(account, folder, uid) );

-- table msg_fts
CREATE VIRTUAL TABLE msg_fts USING fts5(subject, from_name, from_addr, recipients, snippet, body_text, tokenize='unicode61 remove_diacritics 2', content='', contentless_delete=1);

-- table msg_fts_config
CREATE TABLE 'msg_fts_config'(k PRIMARY KEY, v) WITHOUT ROWID;

-- table msg_fts_data
CREATE TABLE 'msg_fts_data'(id INTEGER PRIMARY KEY, block BLOB);

-- table msg_fts_docsize
CREATE TABLE 'msg_fts_docsize'(id INTEGER PRIMARY KEY, sz BLOB, origin INTEGER);

-- table msg_fts_idx
CREATE TABLE 'msg_fts_idx'(segid, term, pgno, PRIMARY KEY(segid, term)) WITHOUT ROWID;

-- table muted
CREATE TABLE muted ( -- silenced threads; keyed on (account,thread_id), NEVER pk account TEXT NOT NULL, thread_id TEXT NOT NULL, muted_at TEXT, PRIMARY KEY(account, thread_id) );

-- table outbox
CREATE TABLE outbox ( id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL, -- the FROM account email (must exist in config) idempotency_key TEXT NOT NULL UNIQUE, -- one logical send per key (dedupes double-submit) message_id TEXT NOT NULL, -- minted at build; stable across retries envelope TEXT NOT NULL, -- JSON [addr-spec,...] full delivery set incl. Bcc (PRIVATE; never in raw) recipients TEXT NOT NULL, -- JSON {to,cc,bcc} for the outbox UI only subject TEXT, raw BLOB NOT NULL, -- Bcc-LESS RFC822 bytes: sent on the wire AND APPENDed to Sent state TEXT NOT NULL DEFAULT 'queued', -- queued | sending | sent | error attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at INTEGER NOT NULL DEFAULT 0, -- unix secs; backoff gate delivered INTEGER NOT NULL DEFAULT 0, -- 1 once the wire send succeeded (never re-send) sent_appended INTEGER NOT NULL DEFAULT 0, -- done-once: Sent copy made answered_set INTEGER NOT NULL DEFAULT 0, -- done-once: \Answered set on original followup_recorded INTEGER NOT NULL DEFAULT 0, -- done-once: sent-no-reply followup seeded last_error TEXT, -- SCRUBBED code only draft_id INTEGER, -- supersede this draft after delivery draft_deleted INTEGER NOT NULL DEFAULT 0, -- done-once: draft removed post-send created_at TEXT NOT NULL, sent_at TEXT , orig_account TEXT, orig_message_id TEXT, orig_thread_id TEXT, wire_begun INTEGER NOT NULL DEFAULT 0, wait_kind TEXT NOT NULL DEFAULT 'grace', original_key INTEGER);

-- table reviews
CREATE TABLE reviews( accounts TEXT NOT NULL, day TEXT NOT NULL, fingerprint TEXT NOT NULL, narrative TEXT NOT NULL, next_json TEXT NOT NULL, stats_json TEXT NOT NULL, needs_json TEXT NOT NULL, waiting_json TEXT NOT NULL, model TEXT, created_at TEXT NOT NULL, PRIMARY KEY(accounts, day));

-- table sender_unsub
CREATE TABLE sender_unsub( from_addr TEXT PRIMARY KEY, raw TEXT NOT NULL, accounts TEXT NOT NULL, updated_at TEXT NOT NULL);

-- table snoozes
CREATE TABLE snoozes( message_key INTEGER NOT NULL REFERENCES message_identity(key) ON DELETE CASCADE, folder TEXT NOT NULL, until INTEGER NOT NULL, PRIMARY KEY(message_key, folder));

-- table sync_state
CREATE TABLE sync_state ( account TEXT NOT NULL, folder TEXT NOT NULL DEFAULT 'INBOX', last_sync TEXT, last_uid INTEGER DEFAULT 0, uidvalidity INTEGER, last_ok_ts TEXT, -- health: last successful sync (added _m15) last_error TEXT, -- exception CLASS name only (scrubbed; never the message) last_error_ts TEXT, last_error_kind TEXT, -- coarse enum: TIMEOUT|AUTH|UIDVALIDITY|IMAP|NETWORK|UNKNOWN sync_count INTEGER DEFAULT 0, scan_state TEXT NOT NULL DEFAULT 'never', highest_modseq INTEGER, PRIMARY KEY(account, folder) );

-- table tasks
CREATE TABLE tasks( id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL, message_key INTEGER NOT NULL, folder TEXT NOT NULL, title TEXT NOT NULL, due TEXT, done_at TEXT, created_at TEXT NOT NULL, UNIQUE(message_key, title));

-- table thread_links
CREATE TABLE thread_links( name TEXT NOT NULL, message_pk INTEGER NOT NULL REFERENCES messages(pk) ON DELETE CASCADE, PRIMARY KEY(name, message_pk));

-- table thread_summaries
CREATE TABLE thread_summaries ( account TEXT NOT NULL, thread_id TEXT NOT NULL, scope TEXT NOT NULL DEFAULT 'thread', -- thread | message fingerprint TEXT NOT NULL, -- content hash; mismatch => stale tldr TEXT, points TEXT, action TEXT, participants TEXT, -- points/participants = JSON message_count INTEGER, model TEXT, created_at TEXT, PRIMARY KEY(account, thread_id, scope) );

-- table undo_entries
CREATE TABLE undo_entries ( id INTEGER PRIMARY KEY AUTOINCREMENT, grp TEXT, -- the undo group the entry gathers; NULL for a single action entry TEXT NOT NULL, -- JSON: kind, label and what to reverse, or a group's children created_at TEXT NOT NULL );

-- table unsub_log
CREATE TABLE unsub_log( id INTEGER PRIMARY KEY, from_addr TEXT, method TEXT, status TEXT, http_status INTEGER, ts TEXT, idempotency_key TEXT UNIQUE, accounts TEXT);

PRAGMA user_version=47;
