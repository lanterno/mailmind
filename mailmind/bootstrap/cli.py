"""What `python -m mailmind <command>` does for the commands that reach mail, rules, views or an
export. Each imports what it needs when it runs, so `db` and `ai` load none of it, and each legacy
module it names is on tests/legacy_ratchet.txt until that code moves into its context.
"""
import imaplib

from mailmind.accounts.domain import provider
from mailmind.bootstrap import curation, discovery, mailbox


def check():
    """Whether each account's credential resolves, printing ok or fail and never a secret."""
    import config
    config.check()


def list_accounts():
    import config
    for a in config.accounts():
        c = a.get("credential")
        style = "plain" if isinstance(c, (str,)) or (isinstance(c, dict) and "plain" in c) else \
                ("op" if isinstance(c, dict) and "op" in c else
                 "env" if isinstance(c, dict) and "env" in c else
                 "proton_bridge" if isinstance(c, dict) and c.get("proton_bridge") else "?")
        print(f"  {a['email']:42} {a['kind']:6} {a['host']}:{a['port']}  cred={style}")


def materialize(only):
    import config
    config.materialize(only)


def sync(who, days):
    """Pull `who`'s mail (an address, or every account) from the last `days` days."""
    import accounts
    import ingest
    import store
    store.init()
    accts = accounts.accounts() if who == "all" else [accounts.get(who)]
    for a in accts:
        try:
            kept, junk, _ = ingest.ingest_account(a, days if days is not None else ingest.CUTOFF_DAYS)
            print(f"{a['email']:<32} kept {kept:>4}  (skipped {junk} junk)", flush=True)
        except Exception as e:
            print(f"{a['email']:<32} ERROR: {type(e).__name__}: {e}", flush=True)
    print("stats:", store.stats(store.conn()))


def assess(limit, workers):
    """Summarize up to `limit` messages with no summary yet, now, at full price."""
    from mailmind.bootstrap import assistant
    import store
    import summarize
    store.init()
    if summarize.engine() == "none":
        print("AI is off: there is no API key, so nothing was summarized.")
        return
    why = assistant.gateway().held_back("triage", summarize.MODEL)
    if why:
        print(f"Triage is held back ({why}), so nothing was summarized.")
        return
    summarize.summarize_pending(limit, workers if workers is not None else summarize.WORKERS,
                                log=lambda m: print(m, flush=True))
    print("stats:", store.stats(store.conn()))


def _count(s, folder):
    try:
        return s.select(folder).exists
    except imaplib.IMAP4.abort:
        raise
    except imaplib.IMAP4.error:
        return 0


def _status(accts):
    import accounts
    for a in accts:
        try:
            s = mailbox.open_session(a)
            inbox = _count(s, "INBOX")
            trash = _count(s, accounts.trash_folder(a))
            print(f"{a['email']:30} INBOX {inbox:>6}   Trash {trash:>6}")
            s.logout()
        except Exception as e:
            print(f"{a['email']:30} ERROR: {type(e).__name__}: {e}")


def _inventory(accts):
    """List every folder with message/unseen counts, for any account type."""
    for a in accts:
        try:
            s = mailbox.open_session(a)
        except Exception as e:
            print(f"\n=== {a['email']} ===\n   ERROR: {type(e).__name__}: {e}")
            continue
        print(f"\n=== {a['email']} ({a['kind']}) ===")
        try:
            for name in [f.name for f in s.folders()]:
                try:
                    uns, msgs = s.status(name)
                    print(f"   {name:<26} {msgs:>7}  unseen {uns}")
                except Exception:
                    print(f"   {name:<26} {'(not selectable)':>7}")
        finally:
            try:
                s.logout()
            except Exception:
                pass


def _expire(accts, dry=False):
    expire = curation.rules.load()[curation.EXPIRE]
    if not expire:
        print("no expiry rules — add one with: python -m mailmind rules expire <sender|domain> <hours>")
        return
    print("rules: " + ", ".join(f"{t} {h}h" for t, h in expire.items()))
    grand_p = grand_m = 0
    for a in accts:
        r = curation.expiry.account(a, dry=dry)
        if r["error"]:
            print(f"{a['email']:38} ERROR: {r['error']}")
            continue
        tag = f"would trash {r['planned']}" if dry else f"moved {r['moved']}/{r['planned']}"
        strag = f"  !{len(r['stragglers'])} stragglers" if r.get("stragglers") else ""
        print(f"{a['email']:38} {tag:>16}{strag}")
        grand_p += r["planned"]; grand_m += r.get("moved", 0)
    print(f"\nTOTAL: expired {grand_p}" + ("" if dry else f" · moved {grand_m}"))


def _sweep_ai(accts, dry=False):
    grand_p = grand_m = grand_k = 0
    for a in accts:
        r = curation.sweeps.sweep_ai(a, dry=dry)
        if r["error"]:
            print(f"{a['email']:38} ERROR: {r['error']}")
            continue
        tag = f"would trash {r['planned']}" if dry else f"moved {r['moved']}/{r['planned']}"
        strag = f"  !{len(r['stragglers'])} stragglers" if r.get("stragglers") else ""
        print(f"{a['email']:38} {tag:>16}   (kept by rule: {r['kept_by_rule']}){strag}")
        grand_p += r["planned"]; grand_m += r.get("moved", 0); grand_k += r["kept_by_rule"]
    print(f"\nTOTAL: planned {grand_p}" + ("" if dry else f" · moved {grand_m}") + f" · kept-by-rule {grand_k}")


def _plan(accts, do_sweep=False, dry=False):
    grand = 0
    for a in accts:
        p = curation.sweeps.plan(a)
        if p["error"]:
            print(f"{a['email']:30} ERROR: {p['error']}")
            continue
        print(f"\n=== {a['email']}  ({p['in_inbox']} in INBOX) ===")
        for k in [curation.SPAM, curation.BLOCKED, curation.MKT, curation.NOTIF,
                  curation.RECEIPT, curation.PROTECTED, curation.PERSONAL]:
            n = p["by_bucket"].get(k, 0)
            if n:
                tag = " TRASH" if k in curation.JUNK_BUCKETS else " keep"
                print(f"   {k:<24} {n:>5}{tag}   {', '.join(p['top_senders'].get(k, []))}")
        print(f"   -> would trash {p['junk']} / keep {p['keep']}")
        grand += p["junk"]
        if do_sweep and not dry and p["junk"]:
            r = curation.sweeps.sweep(a, dry=False)
            msg = f"   swept: moved {r['moved']}/{p['junk']} to Trash (logged -> sweep_audit.jsonl)"
            if r["stragglers"]:
                msg += f"  ! {len(r['stragglers'])} could not be verified-moved"
            print(msg)
    print(f"\nTOTAL junk across accounts: {grand}")


def _empty_trash(accts):
    import accounts
    for a in accts:
        if provider.resolve(a).bulk_expunge_unsafe:
            print(f"{a['email']:30} SKIPPED — empty Proton Trash with the native button "
                  "(Bridge crashes on bulk expunge).")
            continue
        try:
            s = mailbox.open_session(a)
            tf = accounts.trash_folder(a)          # [Gmail]/Trash for gmail, "Trash" for plain IMAP
            s.select(tf, readonly=False)
            uids = s.uids("ALL")
            if not uids:
                print(f"{a['email']:30} Trash already empty")
                s.logout()
                continue
            try:
                for i in range(0, len(uids), 500):
                    s.add_flags(uids[i:i + 500], ["\\Deleted"])
                    s.expunge(uids[i:i + 500])     # these UIDs alone, so nothing another client flagged
            except imaplib.IMAP4.abort:
                pass  # Gmail sometimes drops the connection mid-expunge but completes it
            print(f"{a['email']:30} purged {len(uids)} from Trash")
            try:
                s.logout()
            except Exception:
                pass
        except Exception as e:
            print(f"{a['email']:30} ERROR: {type(e).__name__}: {e}")


def mailboxes(command, who, dry=False):
    """`status`, `inventory`, `plan`, `sweep`, `sweep-ai`, `expire` or `empty-trash` over `who`."""
    import accounts
    accts = accounts.accounts() if who == "all" else [accounts.get(who)]
    accounts.preload_creds()            # one Touch ID up front for all Gmail accounts
    if command == "status":
        _status(accts)
    elif command == "inventory":
        _inventory(accts)
    elif command == "plan":
        _plan(accts, do_sweep=False)
    elif command == "sweep":
        _plan(accts, do_sweep=True, dry=dry)
    elif command == "sweep-ai":
        _sweep_ai(accts, dry=dry)
    elif command == "expire":
        _expire(accts, dry=dry)
    elif command == "empty-trash":
        _empty_trash(accts)


def rules_list():
    r = curation.rules.load()
    for k in curation.rules.KEYS:
        print(f"{k} ({len(r[k])}): {', '.join(r[k]) or '—'}")
    print(f"{curation.rules.EXPIRE} ({len(r[curation.rules.EXPIRE])}): "
          f"{', '.join(f'{t} {h}h' for t, h in r[curation.rules.EXPIRE].items()) or '—'}")


def rules_add(verdict, tokens):
    import ingest                       # it reads the rules through the classifier
    for tok in tokens:
        t, where = curation.rules.add(tok, verdict)
        print(f"  {t} -> {where}; {ingest.reclassify(t)} stored messages classified again")


def rules_remove(tokens):
    import ingest
    for tok in tokens:
        curation.rules.remove(tok)
        print(f"  removed {tok}; {ingest.reclassify(tok)} stored messages classified again")


def rules_expire(token, hours):
    t, h = curation.rules.set_expiry(token, hours)
    print(f"  {t} -> {'expires after ' + str(h) + 'h' if h else 'no expiry'}")


def views_list():
    for v in discovery.views.merged():
        print(f"  {'*' if v['builtin'] else ' '} {v['id']:18} {v['name']:24} {v['q']}")


def views_add(name, query):
    print(discovery.views.add(name, query))


def views_remove(view_id):
    print("removed" if discovery.views.remove(view_id) else "not removed (builtin or missing)")


def export(fmt, out, account, attachments):
    """Write the synced mail out, or with `plan` say what that would hold. Touches no mail server."""
    import export as exporter
    if fmt == "plan":
        p = exporter.plan(account)
        print(f"{p['total']} messages, {p['bodies']} with full bodies, across {len(p['groups'])} folders:")
        for g in p["groups"]:
            print(f"  {g['account']:32} {g['folder']:24} {g['messages']:6}  ({g['bodies']} bodies)")
    elif fmt == "eml":
        print(f"exported {exporter.export_eml(out, account, attachments)} messages to {out}")
    elif fmt == "mbox":
        n, files = exporter.export_mbox(out, account)
        print(f"exported {n} messages to {len(files)} mbox files under {out}")
    elif fmt == "db":
        print("wrote snapshot " + exporter.export_db(out))
