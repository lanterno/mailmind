//! A draft (`compose.application.drafts`): saving it, local first and on its server after (decision D5), removing
//! it, and opening it back into the composer from its own bytes. The reader's side, a draft's id as a message opens,
//! is `drafts`.

use std::fmt;
use std::sync::Arc;
use std::time::Duration;

use mm_kernel::errors::{Failure, Fault, Kind, Problem};
use mm_kernel::log::{self, Field};
use mm_kernel::seams::Clock;
use mm_kernel::when::iso;
use serde_json::{Map, Value, json};

use crate::builder::{build_message, thread_ids};
use crate::composing::{Composing, Composition, REPLIES};
use crate::composition::Composed;
use crate::delivery::Delivery;
use crate::draft;
use crate::draft_journal;
use crate::markdown;
use crate::outbox_item::python_list;
use crate::ports::{Row, Tracked};
use crate::resume::{self as reopening, ResumedFile, TrackedCopy};
use crate::sending::Mint;
use crate::sending_ports::{
    self, Accounts, Book, ComposeStore, Copies, CopyFailed, DraftRow, EVERY_ACCOUNT, Gate, GateWait, Priority, Record,
    Save, Screen, Waiting, on_gate,
};
use crate::spec::Built;

const TAG: &str = "draft";
const RESUME_TAG: &str = "resume";
/// How long a probe of a draft's copy waits for its server.
const LIVE_TIMEOUT: Duration = Duration::from_secs(10);
/// How long a save someone asked for waits for the account's gate, before the push job has it.
pub const PUSH_WAIT: Duration = Duration::from_millis(2500);
const NOT_LOADED: &str = "Couldn\u{2019}t load this draft from the mail server. Try again in a moment.";

/// What wires the drafts.
#[derive(Debug)]
pub struct DraftingParts {
    pub store: Arc<dyn ComposeStore>,
    pub composing: Arc<Composing>,
    pub delivery: Arc<Delivery>,
    pub accounts: Arc<dyn Accounts>,
    pub copies: Arc<dyn Copies>,
    pub screen: Arc<dyn Screen>,
    pub gate: Arc<dyn Gate>,
    pub clock: Arc<dyn Clock>,
    pub mint: Mint,
    /// Whether this process writes to mail servers: a read-only one keeps its saves in the journal.
    pub writes: bool,
}

pub struct Drafting {
    parts: DraftingParts,
}

impl fmt::Debug for Drafting {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Drafting").field("writes", &self.parts.writes).finish_non_exhaustive()
    }
}

/// What a try at the journal came to on its gate.
enum OnGate {
    /// The new copy's UID, None where the server didn't say.
    Pushed(Option<i64>),
    /// Nothing went: it has to wait, or nothing is asked any more.
    Waits,
    /// The row now needs a wider gate than the job held.
    Wider,
}

fn text(row: &Row, name: &str) -> Option<String> {
    row.get(name).and_then(Value::as_str).map(str::to_owned)
}

fn number(row: &Row, name: &str) -> Option<i64> {
    row.get(name).and_then(Value::as_i64)
}

/// The gate a push holds: its account's, or every account's where the old copy is in another (`_scope_of`).
fn scope_of(row: &DraftRow, waiting: &Waiting) -> String {
    if draft::spans_accounts(row, &waiting.account) { EVERY_ACCOUNT.to_owned() } else { waiting.account.clone() }
}

/// What a save records of what the composer held (`draft_journal.record_of`): Bcc is written nowhere in the bytes,
/// so this is all that keeps it.
pub fn record_of(composed: &Composed, built: &Built) -> Record {
    let tokens: Vec<&str> = composed.spec.bcc.iter().map(String::as_str).collect();
    let bcc: Vec<String> = mm_kernel::addresses::parse_addr_list(&tokens)
        .unwrap_or_default()
        .iter()
        .map(|(name, addr)| reopening::addr_chip(name, addr))
        .collect();
    Record {
        raw_sha: reopening::sha_of(&built.raw),
        own_text: reopening::lf(&composed.own),
        tail_text: composed.tail_text.as_deref().filter(|t| !t.is_empty()).map(reopening::lf),
        tail_html: composed.tail_html.clone(),
        bcc: if bcc.is_empty() { None } else { Some(python_list(&bcc)) },
    }
}

/// What a first save tells the composer, so it needs no original from here on (`draft.handed_over`).
fn handed_over(composed: &Composed) -> (Value, Value) {
    let resume = json!({
        "in_reply_to": composed.spec.in_reply_to.clone().unwrap_or_default(),
        "references": composed.spec.references.join(" "),
        "tail_text": reopening::lf(composed.tail_text.as_deref().unwrap_or_default()),
    });
    let files: Vec<Value> = composed
        .forwarded
        .iter()
        .map(|a| {
            json!({"filename": a.filename, "content_type": a.content_type, "size": a.data.len(),
                   "b64": mm_content::frame::base64(&a.data)})
        })
        .collect();
    (resume, Value::Array(files))
}

fn file_json(f: &ResumedFile) -> Value {
    let mut out = Map::new();
    out.insert("filename".into(), json!(f.filename));
    out.insert("content_type".into(), json!(f.content_type));
    out.insert("size".into(), json!(f.size));
    out.insert("b64".into(), json!(f.b64));
    if let Some(cid) = &f.cid {
        out.insert("cid".into(), json!(cid));
    }
    Value::Object(out)
}

impl Drafting {
    pub fn new(parts: DraftingParts) -> Drafting {
        Drafting { parts }
    }

    fn now(&self) -> i64 {
        self.parts.clock.now().whole_seconds()
    }

    fn stamp(&self) -> String {
        iso(self.parts.clock.now())
    }

    fn write<T>(&self, work: impl FnOnce(&dyn Book) -> Result<T, Fault>) -> Result<T, Fault> {
        sending_ports::write(&*self.parts.store, work)
    }

    fn read<T>(&self, work: impl FnOnce(&dyn Book) -> Result<T, Fault>) -> Result<T, Fault> {
        sending_ports::read(&*self.parts.store, work)
    }

    /// Whether a message is a draft: its server flags it \Draft, or it sits in the account's Drafts.
    fn is_draft(&self, row: &Row) -> bool {
        number(row, "draft").unwrap_or(0) != 0
            || self
                .parts
                .screen
                .plays_drafts(&text(row, "account").unwrap_or_default(), &text(row, "folder").unwrap_or_default())
    }

    /// The outbox's `draft_copy_is_ours`, and a copy in a Drafts folder only the last LIST can name.
    fn ours_to_remove(&self, d: &DraftRow) -> bool {
        self.parts.delivery.draft_copy_is_ours(d)
            || self.parts.screen.plays_drafts(&d.account, d.folder.as_deref().unwrap_or_default())
    }

    /// Save a draft, or discard it (`Drafts.save`). Either is local and durable the moment it answers: the journal
    /// holds it, and the server's copy follows. An autosave leaves that to the push job; any other save tries now,
    /// for a moment.
    pub fn save(&self, asked: &Composition) -> Result<Value, Failure> {
        if asked.delete == Some(true)
            && let Some(id) = asked.draft_id.filter(|id| *id != 0)
        {
            return Ok(self.discard(id)?);
        }
        let (composed, _, account) = self.parts.composing.spec(asked)?;
        let spec = &composed.spec;
        if draft::is_empty(&spec.subject, &spec.text, &spec.to, &spec.cc, &spec.bcc, spec.attachments.len()) {
            return Ok(json!({"ok": true, "draft_id": asked.draft_id, "empty": true}));
        }
        let built = build_message(spec, true, self.parts.mint.minting()).map_err(Problem::refused)?;
        let (id, version) = self.accept(asked, &composed, &built, &account.email)?;
        let mut answer = json!({"ok": true, "draft_id": id, "version": version, "message_id": built.message_id,
                                "pushed": false});
        if asked.autosave != Some(true)
            && self.parts.writes
            && let Some(uid) = self.try_push(id, Priority::Interactive, GateWait::For(PUSH_WAIT))
        {
            answer["pushed"] = json!(true);
            answer["uid"] = json!(uid);
        }
        if asked.resume.is_none() && composed.tail_text.as_deref().is_some_and(|t| !t.is_empty()) {
            let (resume, files) = handed_over(&composed);
            answer["resume"] = resume;
            answer["files"] = files;
        }
        Ok(answer)
    }

    /// Write the save to the journal, in one transaction with the draft's row (`_accept`): its id and version. A
    /// save that may not go in is refused, with nothing written.
    fn accept(
        &self,
        asked: &Composition,
        composed: &Composed,
        built: &Built,
        account: &str,
    ) -> Result<(i64, i64), Failure> {
        let given = asked.draft_id.filter(|id| *id != 0);
        let key = draft::composer_key(asked.idempotency_key.as_deref());
        let (now, stamp) = (self.now(), self.stamp());
        let record = record_of(composed, built);
        let mode = asked.mode();
        let autosave = asked.autosave == Some(true);
        let accepted = self.write(|book| -> Result<Result<(i64, i64), Problem>, Fault> {
            let mut prev = match (given, key.as_deref()) {
                (Some(id), _) => book.draft_get(id)?,
                (None, Some(key)) => book.draft_by_key(key)?,
                (None, None) => None,
            };
            let mut waiting = match &prev {
                Some(p) => book.journal_get(p.id)?,
                None => None,
            };
            if draft_journal::asks_removal(waiting.as_ref()) {
                prev = None;
                waiting = None;
            }
            if let Some(p) = &prev {
                if book.outbox_holds_draft(p.id)? {
                    return Ok(Err(Problem::new(Kind::Locked, draft::HELD)));
                }
                if draft::is_stale(p, asked.base_version, key.as_deref()) {
                    return Ok(Err(Problem::new(Kind::Conflict, draft::CHANGED_ELSEWHERE)
                        .with("conflict", json!(true))
                        .with("version", json!(p.version))));
                }
            }
            let version = draft::next_version(prev.as_ref());
            let id = match &prev {
                Some(p) => p.id,
                None => {
                    let id = book.draft_insert(&Tracked {
                        account: account.to_owned(),
                        folder: None,
                        uid: None,
                        message_id: Some(built.message_id.clone()),
                        uidvalidity: None,
                        mode: Some(mode.to_owned()),
                        subject: Some(composed.spec.subject.clone()),
                        at: stamp.clone(),
                    })?;
                    if let Some(key) = &key {
                        book.draft_set_key(id, key)?;
                    }
                    id
                }
            };
            book.draft_note_save(id, Some(mode), Some(&composed.spec.subject), version, key.as_deref(), &stamp)?;
            let (due, attempts) =
                draft_journal::after_save(autosave, now, waiting.as_ref(), prev.as_ref().and_then(|p| p.pushed_at));
            book.journal_put(&Save {
                draft_id: id,
                version,
                account,
                message_id: &built.message_id,
                raw: &built.raw,
                record: &record,
                saved_at: now,
                due_at: due,
                attempts,
            })?;
            Ok(Ok((id, version)))
        })?;
        Ok(accepted?)
    }

    /// Discard a draft (`_discard`). A copy on the server goes first and the row after it, so a server that's away
    /// leaves the journal asking until it's back; with no copy there's nothing to wait for.
    fn discard(&self, id: i64) -> Result<Value, Fault> {
        let (now, stamp) = (self.now(), self.stamp());
        let there = self.write(|book| {
            let Some(row) = book.draft_get(id)? else { return Ok(None) };
            let waiting = book.journal_get(id)?;
            if draft_journal::copy_may_be_there(row.uid, waiting.as_ref()) {
                let version = draft::next_version(Some(&row));
                book.journal_discard(id, version, &row.account, now)?;
                book.draft_note_discard(id, version, &stamp)?;
                return Ok(Some(true));
            }
            book.draft_delete(id)?;
            Ok(Some(false))
        })?;
        let Some(there) = there else { return Ok(json!({"ok": true, "deleted": true})) };
        if there && self.parts.writes {
            self.try_push(id, Priority::Interactive, GateWait::For(PUSH_WAIT));
        }
        self.parts.screen.outbox_changed();
        Ok(json!({"ok": true, "deleted": true}))
    }

    /// Do what the journal asks for this draft, as a job on its gate (`_try_push`): the new copy's UID (None if the
    /// server didn't say), or None when it has to wait: for the gate, for a server, or because nothing is asked.
    fn try_push(&self, id: i64, priority: Priority, wait: GateWait) -> Option<Option<i64>> {
        match self.push_on_gate(id, priority, wait) {
            Ok(OnGate::Pushed(uid)) => Some(uid),
            Ok(OnGate::Waits | OnGate::Wider) => None,
            Err(failed) => {
                log::warning(TAG, "push failed", &[(Field::Id, &id.to_string())], Some(failed.class));
                let now = self.now();
                let counted = self.write(|book| {
                    if let Some(waiting) = book.journal_get(id)? {
                        let attempts = waiting.attempts + 1;
                        book.journal_failed(id, attempts, draft_journal::due_after_failing(now, attempts))?;
                    }
                    Ok(())
                });
                if let Err(fault) = counted {
                    log::warning(TAG, "push failure not counted", &[(Field::Id, &id.to_string())], Some(fault.class));
                }
                self.parts.screen.outbox_changed();
                None
            }
        }
    }

    /// The try itself, the gate busy being a wait like any other.
    fn push_on_gate(&self, id: i64, priority: Priority, wait: GateWait) -> Result<OnGate, CopyFailed> {
        let (waiting, row) = self.read(|book| Ok((book.journal_get(id)?, book.draft_get(id)?)))?;
        let (Some(waiting), Some(row)) = (waiting, row) else { return Ok(OnGate::Waits) };
        let scope = scope_of(&row, &waiting);
        let Ok(done) = on_gate(&*self.parts.gate, &scope, priority, wait, || self.on_gate(id, &scope)) else {
            return Ok(OnGate::Waits);
        };
        match done? {
            OnGate::Wider => {
                let Ok(done) =
                    on_gate(&*self.parts.gate, EVERY_ACCOUNT, priority, wait, || self.on_gate(id, EVERY_ACCOUNT))
                else {
                    return Ok(OnGate::Waits);
                };
                done
            }
            done => Ok(done),
        }
    }

    fn on_gate(&self, id: i64, scope: &str) -> Result<OnGate, CopyFailed> {
        let (waiting, row) = self.read(|book| Ok((book.journal_get(id)?, book.draft_get(id)?)))?;
        let (Some(waiting), Some(row)) = (waiting, row) else { return Ok(OnGate::Waits) };
        if scope != EVERY_ACCOUNT && scope_of(&row, &waiting) != scope {
            return Ok(OnGate::Wider);
        }
        if draft_journal::asks_removal(Some(&waiting)) {
            self.remove(&row, &waiting)?;
            return Ok(OnGate::Waits);
        }
        self.push(&row, &waiting)
    }

    /// APPEND the save, drop the copy it replaces, and move the row with them (`_push`).
    fn push(&self, prev: &DraftRow, j: &Waiting) -> Result<OnGate, CopyFailed> {
        let message_id = j.message_id.clone().unwrap_or_default();
        let going = self.write(|book| book.journal_tried(prev.id, j.version, &message_id))?;
        if !going {
            return Ok(OnGate::Waits);
        }
        if self.parts.accounts.sending(&j.account)?.is_none() {
            return Err(CopyFailed { class: "KeyError", unreachable: false });
        }
        let raw = j.raw.clone().unwrap_or_default();
        let appended = self.parts.copies.append_to_drafts(
            &j.account,
            &raw,
            &draft::replaced_in_place(prev, &j.account),
            Some(&message_id),
            j.tried.as_deref(),
        )?;
        let folder = appended.folder.clone();
        self.parts.screen.touched(&j.account, &folder);
        let mut gone: Vec<(String, String, i64)> =
            appended.removed.map(|uid| (j.account.clone(), folder.clone(), uid)).into_iter().collect();
        // Only now that the new copy is saved, so a save that fails never leaves no copy at all.
        if draft::left_elsewhere(prev, &j.account, &folder) && self.ours_to_remove(prev) {
            let removed = match self.parts.accounts.sending(&prev.account) {
                Ok(Some(_)) => self.parts.copies.delete_draft_uid(
                    &prev.account,
                    prev.folder.as_deref(),
                    prev.uid,
                    prev.uidvalidity,
                ),
                _ => Err(CopyFailed { class: "KeyError", unreachable: false }),
            };
            match removed {
                Ok(true) => {
                    gone.push((prev.account.clone(), prev.folder.clone().unwrap_or_default(), prev.uid.unwrap_or(0)))
                }
                Ok(false) => {}
                Err(failed) => log::warning(TAG, "old copy not removed", &[], Some(failed.class)),
            }
        }
        let record = Record {
            raw_sha: j.raw_sha.clone().unwrap_or_default(),
            own_text: j.own_text.clone().unwrap_or_default(),
            tail_text: j.tail_text.clone(),
            tail_html: j.tail_html.clone(),
            bcc: j.bcc.clone(),
        };
        let (now, version) = (self.now(), j.version);
        self.write(|book| {
            book.draft_settle_push(prev.id, &j.account, &folder, appended.uid, &message_id, appended.uidvalidity, now)?;
            book.draft_set_resume(prev.id, &record)?;
            book.journal_settled(prev.id, version)?;
            for (account, in_folder, old) in &gone {
                book.remove_copies(account, in_folder, &[*old], now)?;
            }
            Ok(())
        })?;
        for (account, in_folder, _) in &gone {
            self.parts.screen.list_changed(account, in_folder);
        }
        self.parts.screen.stats_changed();
        self.parts.screen.outbox_changed();
        Ok(OnGate::Pushed(appended.uid))
    }

    /// A discard's copy goes, and then the row (`_remove`). A connection that fails is a failure, and the journal
    /// asks again; an account that has gone has nothing left to ask.
    fn remove(&self, row: &DraftRow, j: &Waiting) -> Result<(), CopyFailed> {
        let mut removed = false;
        let reach = |email: &str| matches!(self.parts.accounts.sending(email), Ok(Some(_)));
        if reach(&row.account) && row.uid.unwrap_or(0) != 0 && self.ours_to_remove(row) {
            removed =
                self.parts.copies.delete_draft_uid(&row.account, row.folder.as_deref(), row.uid, row.uidvalidity)?;
        }
        if reach(&j.account)
            && let Some(tried) = j.tried.as_deref().filter(|t| !t.is_empty())
        {
            self.parts.copies.delete_left_copy(&j.account, tried)?;
        }
        let now = self.now();
        let folder = row.folder.clone().unwrap_or_default();
        self.write(|book| {
            book.draft_delete(row.id)?;
            if removed {
                book.remove_copies(&row.account, &folder, &[row.uid.unwrap_or(0)], now)?;
            }
            Ok(())
        })?;
        if removed {
            self.parts.screen.list_changed(&row.account, &folder);
        }
        self.parts.screen.outbox_changed();
        Ok(())
    }

    /// One round of the push job (`push_round`): whatever has come due. The seconds until the next is.
    pub fn push_round(&self) -> Result<i64, Fault> {
        let now = self.now();
        let soon = now + draft_journal::PUSH_EVERY;
        for id in self.read(|book| book.journal_due(now, soon))? {
            self.try_push(id, Priority::AfterUser, GateWait::Not);
        }
        let at = self.read(|book| book.journal_next_due(now, soon))?;
        Ok(draft_journal::next_round_in(now, at))
    }

    /// Is this exact copy still on its server (`_live`)? None when that can't be told.
    fn live(&self, account: &str, folder: &str, uid: Option<i64>) -> Option<bool> {
        let uid = uid?;
        let (current, there) = self.parts.copies.probe(account, folder, uid, LIVE_TIMEOUT)?;
        let known = self.read(|book| book.uidvalidity(account, folder)).ok().flatten();
        Some(reopening::copy_is_there(current, known, there))
    }

    /// Open a saved draft back into the composer from its own stored bytes (`Drafts.resume`): by its id where the
    /// request named no copy, else the copy `pk`.
    pub fn resume(&self, draft_id: Option<i64>, pk: Option<i64>) -> Result<Value, Failure> {
        if let Some(id) = draft_id.filter(|id| *id != 0)
            && pk.is_none()
        {
            return self.resume_saved(id);
        }
        let Some(pk) = pk else { return Err(Problem::refused("Missing draft.").into()) };
        let Some(row) = self.read(|book| book.message(pk))? else {
            return Err(Problem::not_found("That draft is no longer in Drafts.").into());
        };
        if !self.is_draft(&row) {
            return Err(Problem::refused("That message isn\u{2019}t a draft.").into());
        }
        let account = text(&row, "account").unwrap_or_default();
        let folder = text(&row, "folder").unwrap_or_default();
        let message_id = text(&row, "message_id");
        if self.parts.accounts.sending(&account)?.is_none() {
            return Err(
                Problem::refused("That draft\u{2019}s account isn\u{2019}t set up in MailMind any more.").into()
            );
        }
        let (before, queued, asked) = self.read(|book| {
            let before = book.draft_by_msgid(&account, message_id.as_deref())?;
            let queued = match &before {
                Some(b) => book.outbox_holds_draft(b.id)?,
                None => false,
            };
            let asked = match &before {
                Some(b) => book.journal_get(b.id)?,
                None => None,
            };
            Ok((before, queued, asked))
        })?;
        if queued {
            return Err(Problem::new(Kind::Locked, draft::HELD).into());
        }
        if draft_journal::asks_removal(asked.as_ref()) {
            return Err(Problem::new(Kind::Conflict, draft::DISCARDED).into());
        }
        let live = self.live(&account, &folder, number(&row, "uid"));
        let fetched = on_gate(&*self.parts.gate, &account, Priority::Interactive, GateWait::Ever, || {
            self.reread_then_fetch(pk, &account, &folder, before.as_ref(), live)
        });
        let raw = match fetched {
            Ok(Ok(Ok(raw))) => raw,
            Ok(Ok(Err(problem))) => return Err(problem.into()),
            Ok(Err(failed)) if failed.unreachable => {
                log::warning(RESUME_TAG, "fetch failed", &[], Some(failed.class));
                return Err(Problem::new(Kind::Unreachable, NOT_LOADED).into());
            }
            Ok(Err(failed)) => {
                log::warning(RESUME_TAG, "draft unreadable", &[], Some(failed.class));
                return Err(Problem::new(Kind::Unreadable, reopening::UNREADABLE).into());
            }
            Err(_) => return Err(Problem::new(Kind::Unreachable, NOT_LOADED).into()),
        };
        if raw.is_empty() {
            return Err(Problem::new(Kind::Unreachable, NOT_LOADED).into());
        }
        let id = self.tracked_id(&row)?;
        let (d, j, current) =
            self.read(|book| Ok((book.draft_get(id)?, book.journal_get(id)?, book.uidvalidity(&account, &folder)?)))?;
        let Some(d) = d else { return Err(Fault::new("DraftGone").into()) };
        let copy = TrackedCopy {
            folder: d.folder.clone(),
            uid: d.uid,
            uidvalidity: d.uidvalidity,
            raw_sha: d.raw_sha.clone(),
        };
        let trust = reopening::trust(&copy, &folder, number(&row, "uid"), current, &raw);
        let mut bytes = raw;
        let mut record = (d.own_text.clone(), d.tail_text.clone(), d.bcc.clone());
        if draft_journal::holds_a_save(j.as_ref())
            && let Some(j) = &j
        {
            if trust.exact {
                bytes = j.raw.clone().unwrap_or_default();
                record = (j.own_text.clone(), j.tail_text.clone(), j.bcc.clone());
            } else {
                self.keep_apart(&d, j)?;
            }
        }
        let repoint = if trust.same_copy {
            None
        } else {
            Some((folder.clone(), number(&row, "uid").unwrap_or(0), current, !trust.exact))
        };
        self.opened(&bytes, &d, &record, trust.exact, &account, text(&row, "subject").as_deref(), repoint)
    }

    /// Under the gate: the body fetched, or a conflict where the copy changed since the list loaded.
    fn reread_then_fetch(
        &self,
        pk: i64,
        account: &str,
        folder: &str,
        before: Option<&DraftRow>,
        live: Option<bool>,
    ) -> Result<Result<Vec<u8>, Problem>, CopyFailed> {
        let now = match before {
            Some(b) => self.read(|book| book.draft_get(b.id))?,
            None => None,
        };
        let seen = |d: Option<&DraftRow>| d.map(|d| (d.uid, d.message_id.clone()));
        if live == Some(false) || reopening::copy_moved(seen(before), seen(now.as_ref())) {
            let at = self.now();
            self.write(|book| book.remove_message(pk, at))?;
            if let Err(failed) = self.parts.copies.sync_whole(account, folder) {
                log::warning(RESUME_TAG, "drafts resync failed", &[], Some(failed.class));
            }
            return Ok(Err(Problem::new(
                Kind::Conflict,
                "This draft changed since the list loaded. Open it again from the refreshed list.",
            )));
        }
        Ok(Ok(self.parts.copies.fetch_body(pk)?.unwrap_or_default()))
    }

    /// The id of the draft row tracking this message, tracked now if it is another app's (`Drafts.info`).
    fn tracked_id(&self, row: &Row) -> Result<i64, Fault> {
        let account = text(row, "account").unwrap_or_default();
        let message_id = text(row, "message_id");
        if let Some(d) = self.read(|book| book.draft_by_msgid(&account, message_id.as_deref()))? {
            return Ok(d.id);
        }
        let folder = text(row, "folder").unwrap_or_default();
        let uidvalidity = self.read(|book| book.uidvalidity(&account, &folder))?;
        let stamp = self.stamp();
        self.write(|book| {
            book.draft_insert(&Tracked {
                account: account.clone(),
                folder: Some(folder.clone()),
                uid: number(row, "uid"),
                message_id: message_id.clone(),
                uidvalidity,
                mode: Some("new".to_owned()),
                subject: text(row, "subject"),
                at: stamp.clone(),
            })
        })
    }

    /// Open a draft by its journal, for one that may not be on the server yet (`_resume_saved`).
    fn resume_saved(&self, id: i64) -> Result<Value, Failure> {
        let (d, j, queued) = self.read(|book| {
            let d = book.draft_get(id)?;
            let queued = d.is_some() && book.outbox_holds_draft(id)?;
            Ok((d, book.journal_get(id)?, queued))
        })?;
        if queued {
            return Err(Problem::new(Kind::Locked, draft::HELD).into());
        }
        let (Some(d), Some(j)) = (d, j) else {
            return Err(Problem::new(Kind::Conflict, "That draft has reached the server. Open it from Drafts.").into());
        };
        if draft_journal::asks_removal(Some(&j)) {
            return Err(Problem::new(Kind::Conflict, draft::DISCARDED).into());
        }
        let record = (j.own_text.clone(), j.tail_text.clone(), j.bcc.clone());
        self.opened(&j.raw.clone().unwrap_or_default(), &d, &record, true, &j.account, d.subject.as_deref(), None)
    }

    /// Another app changed the copy while a save waited here (`_keep_apart`): the save goes on as a draft of its own.
    fn keep_apart(&self, d: &DraftRow, j: &Waiting) -> Result<(), Fault> {
        let stamp = self.stamp();
        self.write(|book| {
            let id = book.draft_insert(&Tracked {
                account: j.account.clone(),
                folder: None,
                uid: None,
                message_id: j.message_id.clone(),
                uidvalidity: None,
                mode: d.mode.clone(),
                subject: d.subject.clone(),
                at: stamp.clone(),
            })?;
            book.draft_note_save(id, d.mode.as_deref(), d.subject.as_deref(), d.version, None, &stamp)?;
            book.journal_move(d.id, id)
        })
    }

    /// A draft's bytes, in the composer's terms (`_opened`). `record` is what MailMind knows of those very bytes:
    /// (own text, quote, Bcc).
    #[allow(clippy::too_many_arguments)]
    fn opened(
        &self,
        raw: &[u8],
        d: &DraftRow,
        record: &(Option<String>, Option<String>, Option<String>),
        exact: bool,
        account: &str,
        subject_then: Option<&str>,
        repoint: Option<(String, i64, Option<i64>, bool)>,
    ) -> Result<Value, Failure> {
        let message = reopening::read(raw)?;
        let (files, inline) = reopening::resume_files(raw)?;
        let (own, tail, how) =
            reopening::split_to_fit(message.text.as_deref(), record.0.as_deref(), record.1.as_deref(), exact, &files)?;
        let (in_reply_to, references) = thread_ids(message.in_reply_to.as_deref(), message.references.as_deref());
        let mode = reopening::mode_of(d.mode.as_deref(), in_reply_to.as_deref());
        let original = match in_reply_to.as_deref().filter(|m| !m.is_empty()) {
            Some(irt) if REPLIES.contains(&mode) => self.read(|book| book.message_by_msgid(account, Some(irt)))?,
            _ => None,
        };
        let (bcc, dropped) = reopening::bcc_of(&message.bcc, record.2.as_deref(), exact);
        if let Some((folder, uid, uidvalidity, forget)) = repoint {
            self.write(|book| book.draft_repoint(d, &folder, uid, uidvalidity, forget))?;
        }
        let (original_key, participants, reply_to, original_from) = match &original {
            Some(o) => {
                let pk = number(o, "pk").unwrap_or(0);
                let stored = self.read(|book| book.body(pk))?.unwrap_or_default();
                (
                    o.get("message_key").cloned().unwrap_or(Value::Null),
                    reopening::participants_of(text(o, "from_addr").as_deref(), text(o, "recipients").as_deref()),
                    mm_content::mime::parse(&stored).reply_to,
                    text(o, "from_addr").unwrap_or_default(),
                )
            }
            None => (Value::Null, Vec::new(), String::new(), String::new()),
        };
        let plain =
            reopening::loses_formatting(how, &own, message.html.as_deref(), &files, &|t, p| markdown::render(t, p));
        Ok(json!({
            "ok": true, "draft_id": d.id, "version": d.version, "mode": mode, "account": account,
            "subject": reopening::subject_of(message.subject.as_deref(), subject_then),
            "to": message.to, "cc": message.cc, "bcc": bcc,
            "text": own, "tail_text": tail, "in_reply_to": in_reply_to.unwrap_or_default(),
            "references": references.join(" "),
            "original_key": original_key, "participants": participants,
            "files": files.iter().map(file_json).collect::<Vec<_>>(), "split": how,
            "reply_to": reply_to, "original_from": original_from,
            "notes": {"inline": inline, "bcc_dropped": dropped, "plain": plain},
        }))
    }
}
