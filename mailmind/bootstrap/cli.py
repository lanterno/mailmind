"""What `python -m mailmind <command>` does for the commands that reach mail, rules, views or an
export. Each imports what it needs when it runs, so `db` and `ai` load none of it, and each legacy
module it names is on tests/legacy_ratchet.txt until that code moves into its context.
"""


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


def mailboxes(command, who, dry=False):
    """`status`, `inventory`, `plan`, `sweep`, `sweep-ai`, `expire` or `empty-trash` over `who`."""
    import accounts
    import manage
    accts = accounts.accounts() if who == "all" else [accounts.get(who)]
    accounts.preload_creds()            # one Touch ID up front for all Gmail accounts
    if command == "status":
        manage.cmd_status(accts)
    elif command == "inventory":
        manage.cmd_inventory(accts)
    elif command == "plan":
        manage.cmd_plan(accts, do_sweep=False)
    elif command == "sweep":
        manage.cmd_plan(accts, do_sweep=True, dry=dry)
    elif command == "sweep-ai":
        manage.cmd_sweep_ai(accts, dry=dry)
    elif command == "expire":
        manage.cmd_expire(accts, dry=dry)
    elif command == "empty-trash":
        manage.cmd_empty_trash(accts)


def rules_list():
    import rules
    r = rules.load()
    for k in rules.KEYS:
        print(f"{k} ({len(r[k])}): {', '.join(r[k]) or '—'}")
    print(f"{rules.EXPIRE} ({len(r[rules.EXPIRE])}): "
          f"{', '.join(f'{t} {h}h' for t, h in r[rules.EXPIRE].items()) or '—'}")


def rules_add(verdict, tokens):
    import ingest                       # it reads the rules through the classifier
    import rules
    for tok in tokens:
        t, where = rules.add(tok, verdict)
        print(f"  {t} -> {where}; {ingest.reclassify(t)} stored messages classified again")


def rules_remove(tokens):
    import ingest
    import rules
    for tok in tokens:
        rules.remove(tok)
        print(f"  removed {tok}; {ingest.reclassify(tok)} stored messages classified again")


def rules_expire(token, hours):
    import rules
    t, h = rules.set_expiry(token, hours)
    print(f"  {t} -> {'expires after ' + str(h) + 'h' if h else 'no expiry'}")


def views_list():
    import views
    for v in views.merged():
        print(f"  {'*' if v['builtin'] else ' '} {v['id']:18} {v['name']:24} {v['q']}")


def views_add(name, query):
    import views
    print(views.add(name, query))


def views_remove(view_id):
    import views
    print("removed" if views.remove(view_id) else "not removed (builtin or missing)")


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
