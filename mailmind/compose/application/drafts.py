"""A draft: saving it on the server, removing it, and opening it back into the composer from its own bytes."""
import base64
import email
import email.policy
import hashlib
import json

from mailmind.compose.application.composing import composer_key
from mailmind.compose.domain import limits
from mailmind.compose.domain.builder import build_message, thread_ids
from mailmind.compose.domain.errors import Refused
from mailmind.compose.domain.resume import MODES, PARTICIPANT, lf, resume_files, resume_split
from mailmind.content import mime
from mailmind.content.headers import addr_chip, header_chips, header_text
from mailmind.kernel import addresses
from mailmind.kernel.when import now_iso
from mailmind.platform import profile
from mailmind.platform.workers import gates

_LIVE_TIMEOUT = 10


class Drafts:
    def __init__(self, composing, outbox, book, accounts, mail, copies, screen):
        self._composing, self._outbox, self._book = composing, outbox, book
        self._accounts, self._mail, self._copies, self._screen = accounts, mail, copies, screen

    def is_draft(self, row):
        """A message is a draft if the server flags it \\Draft, or it sits in the account's Drafts."""
        return bool(row["draft"]) or self._screen.plays_drafts(row["account"], row["folder"])

    def _ours_to_remove(self, d):
        """The outbox's draft_copy_is_ours, and a copy in a Drafts folder only the last LIST can name."""
        return self._outbox.draft_copy_is_ours(d) or self._screen.plays_drafts(d["account"], d["folder"])

    def info(self, row):
        """For a draft, expose its draft_id + mode so the client can open it back into the composer.
        Lazily registers a drafts row if one isn't tracked yet."""
        if not self.is_draft(row):
            return {}
        c = self._book.connect()
        d = self._book.draft_by_msgid(c, row["account"], row["message_id"])
        if not d:
            now = now_iso()
            did = self._book.draft_upsert(c, {"id": None, "account": row["account"], "folder": row["folder"],
                                         "uid": row["uid"], "message_id": row["message_id"],
                                         "uidvalidity": self._mail.uidvalidity(row["account"], row["folder"]),
                                         "in_reply_to_pk": None, "mode": "new",
                                         "subject": row["subject"], "created_at": now, "updated_at": now})
            c.commit()
            mode = "new"
        else:
            did, mode = d["id"], (d["mode"] or "new")
        c.close()
        return {"draft_id": did, "draft_mode": mode, "is_draft": True}

    def save(self, body):
        """Save (APPEND, dedup-on-resave) or delete a draft. Returns (result, code)."""
        profile.refuse_writes("a draft save")    # a draft lives on the server; nothing queues it yet
        if body.get("delete") and body.get("draft_id"):
            c = self._book.connect()
            row = self._book.draft_get(c, int(body["draft_id"]))
            c.close()
            if row:
                removed = False
                if self._ours_to_remove(row):
                    try:
                        acct = self._accounts.get(row["account"])
                        removed = gates.run(acct["email"], lambda: self._copies.delete_draft_uid(
                            acct, row["folder"], row["uid"], row["uidvalidity"]), priority=gates.Priority.INTERACTIVE)
                    except Exception as e:
                        print(f"[draft] delete failed: {self._screen.quiet_error(e)}", flush=True)
                c = self._book.connect()
                try:
                    self._book.draft_delete(c, int(body["draft_id"]))
                    if removed:
                        self._mail.remove_copies(c, row["account"], row["folder"], [row["uid"]])
                    c.commit()
                finally:
                    c.close()
                if removed:
                    self._screen.list_changed(row["account"], row["folder"])
            return {"ok": True, "deleted": True}, 200
        try:
            spec, in_reply_to_pk, acct = self._composing.spec(body)
            # nothing to save? avoid littering the Drafts folder with an empty message
            if not (spec["subject"].strip() or (spec["text"] or "").strip()
                    or spec["to"] or spec["cc"] or spec["bcc"] or spec["attachments"]):
                return {"ok": True, "draft_id": body.get("draft_id"), "empty": True}, 200
            built = build_message(spec, draft=True)
        except Refused as h:
            return {"ok": False, "error": h.msg}, h.code
        except ValueError as e:
            return {"ok": False, "error": str(e)}, 400
        did = int(body["draft_id"]) if body.get("draft_id") else None
        key = composer_key(body)

        def replaces():
            """The row this save takes over: the one it names, or for a first save the one an earlier
            save under the composer's key made, whose answer the composer never got."""
            c = self._book.connect()
            try:
                if did:
                    return self._book.draft_get(c, did)
                return self._book.draft_by_key(c, key) if key else None
            finally:
                c.close()

        def scope_for(row):
            # an old copy in another account (From changed) is removed there too: the job spans both
            return acct["email"] if row is None or row["account"] == acct["email"] else gates.EVERY_ACCOUNT

        def save(scope):
            """APPEND the new copy, drop the old one, and move the row with them. Returns the row's id
            and the new copy's UID, or None if the row now needs a wider gate than `scope`."""
            prev = replaces()    # under the gate: a save this one waited for has moved the row on
            if scope != gates.EVERY_ACCOUNT and scope_for(prev) != scope:
                return None
            same = prev is not None and prev["account"] == acct["email"]   # From changed → prev's UID is another mailbox's
            folder, uid, uidv, replaced = self._copies.append_to_drafts(
                acct, built["raw"],
                prev_uid=(prev["uid"] if same else None),
                prev_uidvalidity=(prev["uidvalidity"] if same else None),
                prev_msgid=(prev["message_id"] if same else None),
                prev_folder=(prev["folder"] if same else None))
            self._screen.touched(acct["email"], folder)       # its list shows the new copy on its next open
            gone = [(acct["email"], folder, replaced)] if replaced else []   # and no longer the copies it replaced
            # The old copy is in another mailbox (From changed) or another folder (a draft reopened
            # from elsewhere): drop it there, and only now that the new copy is saved, so a save that
            # fails never leaves no copy at all. Best-effort; a UID we can't vouch for stays, and so
            # does a message that isn't a draft at all.
            if (prev and prev["uid"] and prev["uidvalidity"] is not None
                    and (not same or (prev["folder"] and prev["folder"] != folder))
                    and self._ours_to_remove(prev)):
                try:
                    if self._copies.delete_draft_uid(self._accounts.get(prev["account"]), prev["folder"], prev["uid"],
                                                 prev["uidvalidity"]):
                        gone.append((prev["account"], prev["folder"], prev["uid"]))
                except Exception as e:
                    print(f"[draft] old copy not removed: {self._screen.quiet_error(e)}", flush=True)
            # the row moves with the copy, under the same gate: Edit re-checks it under the gate
            now = now_iso()
            row = {"id": prev["id"] if prev else None, "account": acct["email"], "folder": folder, "uid": uid,
                   "message_id": built["message_id"], "uidvalidity": uidv, "in_reply_to_pk": in_reply_to_pk,
                   "mode": body.get("mode", "new"), "subject": spec["subject"],
                   "created_at": now, "updated_at": now}
            bcc = [addr_chip(n, a) for n, a in addresses.parse_addr_list(spec["bcc"])]   # validated by build
            c = self._book.connect()
            try:
                new_id = self._book.draft_upsert(c, row)
                self._book.draft_set_resume(c, new_id, lf(spec["own"]),
                                       lf(spec["tail"]["text"]) if spec["tail"]["text"] else None,
                                       spec["tail"]["html"], json.dumps(bcc) if bcc else None,
                                       hashlib.sha256(built["raw"]).hexdigest())
                if key and not did:
                    self._book.draft_set_key(c, new_id, key)
                for account, in_folder, old_uid in gone:
                    self._mail.remove_copies(c, account, in_folder, [old_uid])
                c.commit()
            finally:
                c.close()
            for account, in_folder, _ in gone:
                self._screen.list_changed(account, in_folder)
            return new_id, uid
        try:
            scope = scope_for(replaces())
            saved = gates.run(scope, lambda: save(scope), priority=gates.Priority.INTERACTIVE)
            if saved is None:    # another save moved the row to another account while this one waited
                saved = gates.run(gates.EVERY_ACCOUNT, lambda: save(gates.EVERY_ACCOUNT),
                                  priority=gates.Priority.INTERACTIVE)
            new_id, uid = saved
        except ValueError as e:
            return {"ok": False, "error": str(e)}, 400
        except Exception as e:
            print(f"[draft] save failed: {self._screen.quiet_error(e)}", flush=True)
            return {"ok": False, "error": "could not save draft"}, 502
        self._screen.stats_changed()
        answer = {"ok": True, "draft_id": new_id, "uid": uid, "message_id": built["message_id"]}
        if body.get("resume") is None and spec["tail"]["text"]:
            # From here on the composer names the quote, the threading and the files itself, as a
            # reopened draft's does, so its saves and its send no longer need the original's row.
            answer["resume"] = {"in_reply_to": spec["in_reply_to"] or "", "references": " ".join(spec["references"] or []),
                                "tail_text": lf(spec["tail"]["text"])}
            answer["files"] = [{"filename": a["filename"], "content_type": a["content_type"], "size": len(a["data"]),
                                "b64": base64.b64encode(a["data"]).decode("ascii")} for a in spec["forwarded"]]
        return answer, 200

    def _live(self, acct, folder, uid):
        """Is this exact draft copy still on the server? True, False, or None when that can't be told
        (unreachable, no UID). A Drafts list loaded before an autosave still shows the copy that save
        replaced, and reopening it would put an older text over the newer one. A stateless, read-only
        probe with a short timeout, like the account test, so it needs no IMAP gate: a black-holed
        host costs Edit seconds, not a minute of every other mail operation."""
        if uid is None:
            return None
        seen = self._copies.probe(acct, folder, uid, _LIVE_TIMEOUT)
        if seen is None:
            return None
        cur, there = seen
        known = self._mail.uidvalidity(acct["email"], folder)
        if cur is not None and known is not None and cur != known:
            return False                                     # every UID we hold for this folder is void
        return there

    def resume(self, body):
        """Open a saved draft back into the composer from its own stored bytes: your text, the quote
        or forwarded section it already carries (as text), every file as base64, To/Cc/Bcc as chips
        and its own threading headers. The original is looked up by (account, Message-ID) for the
        composer's sake only: sending a reopened draft never needs it. What MailMind recorded at its
        last save (_m20_draft_resume) is trusted only for the very copy it saved. Returns (result, code)."""
        try:
            pk = int(body.get("pk"))
        except (TypeError, ValueError):
            return {"ok": False, "error": "Missing draft."}, 400
        row = self._mail.message(pk)
        if not row:
            return {"ok": False, "error": "That draft is no longer in Drafts."}, 404
        if not self.is_draft(row):
            return {"ok": False, "error": "That message isn’t a draft."}, 400
        try:
            acct = self._accounts.get(row["account"])
        except KeyError:
            return {"ok": False, "error": "That draft’s account isn’t set up in MailMind any more."}, 400
        unreachable = {"ok": False, "error": "Couldn’t load this draft from the mail server. Try again in a moment."}
        elsewhere = " Edit it in the app that made it."
        unreadable = {"ok": False, "error": "MailMind can’t read this draft." + elsewhere}
        c = self._book.connect()
        before = self._book.draft_by_msgid(c, row["account"], row["message_id"])
        queued = before is not None and self._book.outbox_holds_draft(c, before["id"])
        c.close()
        if queued:                                           # reopened and sent again, it would go out twice
            return {"ok": False, "error": "This draft is in the outbox, waiting to be sent. Cancel it there "
                                          "first to change it."}, 423
        live = self._live(acct, row["folder"], row["uid"])  # unreachable (None) → the local copy

        def reread_then_fetch():
            """The 409 when the copy changed, else None once the body is fetched."""
            # the probe ran before the gate: a save or a send that held it meanwhile may have replaced
            # or removed the copy the row tracked (do_draft and the outbox move the row under it too)
            c = self._book.connect()
            now = self._book.draft_get(c, before["id"]) if before is not None else None
            c.close()
            moved = before is not None and (now is None or (now["uid"], now["message_id"])
                                            != (before["uid"], before["message_id"]))
            if live is False or moved:
                self._mail.remove(pk)
                try:                                         # full: only a full scan brings back older UIDs
                    self._mail.sync_whole(acct, row["folder"])
                except Exception as e:
                    print(f"[resume] drafts resync failed: {self._screen.quiet_error(e)}", flush=True)
                return {"ok": False, "error": "This draft changed since the list loaded. "
                                              "Open it again from the refreshed list."}, 409
            self._mail.fetch_body(pk)
            return None
        try:
            changed = gates.run(acct["email"], reread_then_fetch, priority=gates.Priority.INTERACTIVE)
        except self._mail.unreachable() as e:
            print(f"[resume] fetch failed: {self._screen.quiet_error(e)}", flush=True)
            return unreachable, 502
        except Exception as e:                               # fetched, but its bytes don't parse: retrying won't help
            print(f"[resume] draft unreadable: {type(e).__name__}", flush=True)
            return unreadable, 422
        if changed:
            return changed
        raw = self._mail.body(pk)
        if not raw:
            return unreachable, 502
        try:
            msg = email.message_from_bytes(raw, policy=email.policy.default)
            parsed = mime.parse(raw)
        except Exception as e:
            print(f"[resume] draft unreadable: {type(e).__name__}", flush=True)
            return unreadable, 422
        try:
            files, inline = resume_files(msg, parsed, elsewhere)
        except Refused as h:
            return {"ok": False, "error": h.msg}, h.code
        except Exception as e:
            print(f"[resume] draft unreadable: {type(e).__name__}", flush=True)
            return unreadable, 422
        did = self.info(row)["draft_id"]                   # registers another app's draft
        c = self._book.connect()
        d = self._book.draft_get(c, did)
        c.close()
        cur = self._mail.uidvalidity(row["account"], row["folder"])
        same_copy = (d["uid"] is not None and d["uid"] == row["uid"] and d["folder"] == row["folder"]
                     and (d["uidvalidity"] is None or cur is None or d["uidvalidity"] == cur))
        exact = bool(same_copy or (d["raw_sha"] and d["raw_sha"] == hashlib.sha256(raw).hexdigest()))
        split = resume_split(parsed["text"], {"own_text": d["own_text"], "tail_text": d["tail_text"]}, exact)
        if split is None:
            return {"ok": False, "error": "This draft only has formatted text. Edit it in the app that made it, "
                                          "so its links and formatting aren’t lost."}, 422
        own, tail, how = split
        # every save and send of it carries the files as base64 and the text and quote as JSON: past
        # limits.MAX_SEND_POST each one would be refused, and edits made in the composer lost on close
        size = sum(len(f["b64"]) for f in files) + sum(len(json.dumps(t, ensure_ascii=False).encode())
                                                       for t in (own, tail))
        if size + limits.RESUME_HEADROOM > limits.MAX_SEND_POST:
            return {"ok": False, "error": "This draft is too large for MailMind to save or send, with its files "
                                          "and the text it quotes." + elsewhere}, 422
        irt, refs = thread_ids(header_text(msg, "In-Reply-To"), header_text(msg, "References"))
        mode = d["mode"] if d["mode"] in MODES else "new"
        if mode == "new" and irt:                            # MailMind's own new drafts never carry one
            mode = "reply"
        orig_pk, participants = None, []
        if mode in ("reply", "replyall") and irt:
            orow = self._mail.by_msgid(row["account"], irt)
            if orow:
                orig_pk = orow["pk"]
                for a in [orow["from_addr"] or ""] + PARTICIPANT.findall(orow["recipients"] or ""):
                    a = a.strip().lower()
                    if a and a not in participants:
                        participants.append(a)
        def key(chip):
            try:
                return addresses.parse_addr_list([chip])[0][1].lower()
            except (ValueError, IndexError):
                return ""
        hdr = header_chips(msg, "Bcc")                         # another app may keep Bcc in the draft itself
        had = {key(x) for x in hdr}
        missing = [x for x in (json.loads(d["bcc"]) if d["bcc"] else []) if key(x) and key(x) not in had]
        bcc, dropped = (hdr + missing, 0) if exact else (hdr, len(missing))   # the record is only this copy's
        if not same_copy:                                    # the next save or send replaces the copy opened
            c = self._book.connect()
            self._book.draft_repoint(c, d, row["folder"], row["uid"], cur, forget=not exact)
            c.commit()
            c.close()
        subject = header_text(msg, "Subject")                 # as drafted: the synced row's copy is unfolded with spaces
        return {"ok": True, "draft_id": did, "mode": mode, "account": row["account"],
                "subject": subject if subject is not None else (row["subject"] or ""),
                "to": header_chips(msg, "To"), "cc": header_chips(msg, "Cc"), "bcc": bcc,
                "text": own, "tail_text": tail, "in_reply_to": irt or "", "references": " ".join(refs),
                "orig_pk": orig_pk, "participants": participants, "files": files, "split": how,
                "notes": {"inline": inline, "bcc_dropped": dropped, "plain": how == "whole" and bool(parsed["html"])}}, 200
