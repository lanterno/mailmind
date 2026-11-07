// Email connections: who MailMind reads, and how it reaches each. A password goes to the server once and never comes back;
// what the page shows of a stored one is only how it is kept.
import { useEffect, useState, useEffectEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Dialog, Modal, ModalOverlay } from "react-aria-components";
import { get, post } from "../../lib/api/client";
import type { GetAnswers } from "../../lib/api/types.gen";
import { avatarHue, relWhen, shortAddress } from "../../lib/text";
import { toast } from "../../lib/toast";
import { Icon } from "../../lib/ui/Icon";
import { useFocusFirst } from "../../lib/ui/menuKeys";
import { useDigest } from "../mailbox";
import { syncNow } from "../status";
import { settingsOpen } from "./open";
import { useLayer } from "../../lib/backTrap";

type Connection = NonNullable<GetAnswers["/api/accounts"]["accounts"]>[number];
type Health = GetAnswers["/api/account-health"]["accounts"][number];
type Credential = { set?: boolean; style?: string; ref?: string | string[]; var?: string };
type Kind = "gmail" | "imap" | "proton";
type Cred = "plain" | "op" | "env" | "proton_bridge";
type Tested = { kind: "run" | "ok" | "err"; text: string } | null;

const credentialOf = (c: Connection) => c.credential as unknown as Credential;
const refOf = (c: Credential | null | undefined) => (Array.isArray(c?.ref) ? c.ref[0] : c?.ref) ?? "";

async function testConnection(payload: unknown, say: (t: Tested) => void) {
  say({ kind: "run", text: "Testing… (approve Touch ID if asked)" });
  try {
    const d = await post("/api/accounts/test", payload);
    if (d.ok && d.connect) say({ kind: "ok", text: "✓ Connected" });
    else say({ kind: "err", text: `✕ ${d.error || "failed"}` });
  } catch {
    say({ kind: "err", text: "✕ test failed" });
  }
}

function TestResult({ t }: { t: Tested }) {
  return <span className={t ? `set-status ${t.kind}` : "set-status"} role="status">{t?.text}</span>;
}

function Seg<T extends string>({ value, options, label, onChange }: { value: T; options: [T, string][]; label: string; onChange: (v: T) => void }) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map(([v, words]) => <button type="button" key={v} className={v === value ? "on" : ""} aria-pressed={v === value} onClick={() => onChange(v)}>{words}</button>)}
    </div>
  );
}

function ConnectionForm({ row, signatureDefault, done, cancel }: { row: Connection | null; signatureDefault: string; done: (added: boolean) => void; cancel: () => void }) {
  const was = row ? credentialOf(row) : null;
  const inside = useFocusFirst<HTMLDivElement>("input");
  const [alias, setAlias] = useState(row?.alias ?? "");
  const [signature, setSignature] = useState(row?.signature ?? "");
  const [email, setEmail] = useState(row?.email ?? "");
  const [type, setType] = useState<Kind>((row?.type as Kind) ?? "gmail");
  const [host, setHost] = useState(row?.host ?? "");
  const [port, setPort] = useState(String(row?.port ?? 993));
  const [security, setSecurity] = useState<"tls" | "starttls">(row?.starttls ? "starttls" : "tls");
  const [style, setStyle] = useState<Exclude<Cred, "proton_bridge">>(was?.style === "op" || was?.style === "env" ? was.style : "plain");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [opRef, setOpRef] = useState(was?.style === "op" ? refOf(was) : "");
  const [envVar, setEnvVar] = useState(was?.style === "env" ? was.var ?? "" : "");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [tested, setTested] = useState<Tested>(null);

  const credential = () => {
    if (type === "proton") return { style: "proton_bridge" };
    if (style === "plain") return password.trim() ? { style: "plain", value: password.trim() } : null;
    if (style === "op") return opRef.trim() ? { style: "op", ref: opRef.trim() } : null;
    return envVar.trim() ? { style: "env", var: envVar.trim() } : null;
  };
  const imapBits = () => (type === "imap" ? { host: host.trim(), port: Number(port) || 993, starttls: security === "starttls" } : {});
  const test = () => {
    const c = credential();
    if (c) void testConnection({ draft: { email: row ? row.email : email.trim(), type, ...imapBits(), credential: c } }, setTested);
    else if (row) void testConnection({ email: row.email }, setTested);
    else setTested({ kind: "err", text: "enter a password to test" });
  };
  const save = async () => {
    setError("");
    const c = credential(), address = row ? row.email : email.trim();
    if (!row && !address.includes("@")) return setError("A valid email is required.");
    if (type === "imap" && !host.trim()) return setError("A host is required for a generic IMAP account.");
    if (!row && !c) return setError("Set a password (or 1Password ref / env var).");
    setSaving(true);
    try {
      const d = await post(row ? "/api/accounts/edit" : "/api/accounts/add", { alias: alias.trim(), signature: signature.trim(), type, email: address, ...imapBits(), ...(c ? { credential: c } : {}) });
      if (!d.ok) {
        setError((d as { error?: string }).error || "Could not save.");
        setSaving(false);
        return;
      }
      toast(`${row ? "Updated" : "Added"} ${shortAddress(address)}`);
      done(!row);
    } catch {
      setError("Save failed.");
      setSaving(false);
    }
  };
  return (
    <div className="set-form" ref={inside}>
      <label className="lbl" htmlFor="setAlias">Alias (optional)</label>
      <input type="text" id="setAlias" value={alias} placeholder="Friendly name shown on the dots & rows" onChange={(e) => setAlias(e.target.value)} />
      <label className="lbl" htmlFor="setSig">Signature (optional)</label>
      <textarea id="setSig" rows={3} className="set-sig" value={signature} placeholder={`Appended to new mail and above quoted replies${signatureDefault ? " — blank uses the global default" : ""}`} onChange={(e) => setSignature(e.target.value)} />
      <label className="lbl" htmlFor="setEmail">Email</label>
      <input type="email" id="setEmail" value={email} readOnly={Boolean(row)} placeholder="you@example.com" onChange={(e) => setEmail(e.target.value)} />
      {row && <div className="set-help">Identity is fixed — remove and re-add to change the address.</div>}
      <div className="lbl">Type</div>
      <Seg label="Type" value={type} options={[["gmail", "Gmail"], ["imap", "IMAP"], ["proton", "Proton"]]} onChange={setType} />
      {type === "imap" && (
        <div>
          <div className="twocol">
            <div><label className="lbl" htmlFor="setHost">Host</label><input type="text" id="setHost" value={host} placeholder="mail.yourhost.com" onChange={(e) => setHost(e.target.value)} /></div>
            <div><label className="lbl" htmlFor="setPort">Port</label><input type="number" id="setPort" value={port} onChange={(e) => setPort(e.target.value)} /></div>
          </div>
          <Seg label="Security" value={security} options={[["tls", "TLS"], ["starttls", "STARTTLS"]]} onChange={setSecurity} />
        </div>
      )}
      <div className="lbl">Credential</div>
      {type === "proton"
        ? (
          <>
            <Seg label="Credential" value="proton_bridge" options={[["proton_bridge", "Proton Bridge"]]} onChange={() => {}} />
            <div className="set-help">Uses the local Proton Bridge password (env MAILMIND_PROTON_BRIDGE_PASS / op / ~/.config/mailmind).</div>
          </>
        )
        : (
          <>
            <Seg label="Credential" value={style} options={[["plain", "Password"], ["op", "1Password"], ["env", "Env var"]]} onChange={setStyle} />
            {style === "plain" && (
              <>
                <div className="pwwrap">
                  <input type={showPassword ? "text" : "password"} aria-label="Password" value={password} autoComplete="new-password" placeholder={row ? "•••••••• unchanged — leave blank to keep" : "paste the password / app password"} onChange={(e) => setPassword(e.target.value)} />
                  <button type="button" className="eye" aria-label={showPassword ? "Hide password" : "Show password"} onClick={() => setShowPassword(!showPassword)}><Icon name="eye" /></button>
                </div>
                {type === "gmail" && <div className="set-help">Gmail needs an App Password (2-Step Verification → App passwords), not your login password.</div>}
              </>
            )}
            {style === "op" && (
              <>
                <input type="text" aria-label="1Password reference" value={opRef} placeholder="op://Vault/Item/Field" onChange={(e) => setOpRef(e.target.value)} />
                <div className="set-help">Read from the 1Password CLI at runtime.</div>
              </>
            )}
            {style === "env" && (
              <>
                <input type="text" aria-label="Environment variable" value={envVar} placeholder="ENV_VAR_NAME" onChange={(e) => setEnvVar(e.target.value)} />
                <div className="set-help">Read from an environment variable.</div>
              </>
            )}
          </>
        )}
      <div className="err" role="alert">{error}</div>
      <div className="formbar">
        <button className="set-mini" onClick={test}>Test connection</button><TestResult t={tested} />
        <button className="abtn" style={{ marginLeft: "auto" }} onClick={cancel}>Cancel</button>
        <button className="abtn prim" disabled={saving} onClick={() => void save()}>{saving ? "Saving…" : "Save connection"}</button>
      </div>
    </div>
  );
}

function Credentials({ c }: { c: Credential | null }) {
  if (!c?.set) return <span className="set-cred">no password set</span>;
  if (c.style === "plain") return <span className="set-cred"><span className="lock"><Icon name="lock" /></span> ••••••••</span>;
  if (c.style === "op") return <span className="set-cred"><Icon name="key" /> {refOf(c)}</span>;
  if (c.style === "env") return <span className="set-cred">env ${c.var}</span>;
  if (c.style === "proton_bridge") return <span className="set-cred">Proton Bridge</span>;
  return <span className="set-cred">—</span>;
}

function HealthLine({ h, email, again }: { h: Health | undefined; email: string; again: () => void }) {
  const [busy, setBusy] = useState(false);
  if (!h) return null;
  const act = async (path: "/api/accounts/trust-cert" | "/api/sync-retry-account", body: object, ok: string, no: string) => {
    setBusy(true);
    try {
      await post(path, body);
      toast(ok);
      setTimeout(again, 2500);
    } catch {
      toast(no);
      again();
    }
  };
  const when = h.last_error_ts ? ` · ${relWhen(h.last_error_ts)}` : "";
  if (h.last_error && h.last_error_kind === "TLS_PIN") {
    return (
      <div className="set-health err">
        <Icon name="alert" /> certificate changed{when}: the mail server on this computer showed a different certificate, so MailMind sent no password. If you reinstalled or reset Proton Bridge, trust the new one.{" "}
        <button className="set-mini" disabled={busy} onClick={() => void act("/api/accounts/trust-cert", { email }, `Trusted · re-syncing ${shortAddress(email)}`, "Couldn’t trust it")}>{busy ? "Trusting…" : "Trust it"}</button>
      </div>
    );
  }
  if (h.last_error && h.last_error_kind) {
    return (
      <div className="set-health err">
        <Icon name="alert" /> sync error · {h.last_error_kind}{when}{" "}
        <button className="set-mini" disabled={busy} onClick={() => void act("/api/sync-retry-account", { account: email }, `Re-syncing ${shortAddress(email)}`, "Couldn’t retry")}>{busy ? "↻ Syncing…" : "↻ Retry"}</button>
      </div>
    );
  }
  if (h.last_ok_ts) return <div className="set-health ok">✓ synced {relWhen(h.last_ok_ts)} · {h.sync_count} sync{h.sync_count === 1 ? "" : "s"}</div>;
  return null;
}

function ConnectionRow({ r, h, status, edit, again }: { r: Connection; h: Health | undefined; status: string; edit: () => void; again: () => void }) {
  const qc = useQueryClient();
  const [tested, setTested] = useState<Tested>(null);
  const [arming, setArming] = useState(false);
  useEffect(() => {
    if (!arming) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();   // out of asking, and no further
      setArming(false);
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [arming]);
  const label = r.alias || shortAddress(r.email);
  useEffect(() => {
    if (!arming) return;
    const t = setTimeout(() => setArming(false), 3000);
    return () => clearTimeout(t);
  }, [arming]);
  const remove = async () => {
    if (!arming) return setArming(true);
    setArming(false);
    try {
      const d = await post("/api/accounts/delete", { email: r.email });
      if (d.ok) {
        toast(`Removed ${shortAddress(r.email)}`);
        again();
        void qc.invalidateQueries({ queryKey: ["digest"] });
      } else toast(d.error || "Couldn’t remove");
    } catch {
      toast("Remove failed");
    }
  };
  return (
    <div className={r.disabled ? "set-row disabled" : "set-row"} data-email={r.email}>
      <div className={`chip-dot ${status}`} style={{ color: `hsl(${avatarHue(r.email)} var(--mono-s) var(--mono-l))` }}>{(label[0] || "?").toUpperCase()}<span className="led" /></div>
      <div className="who">
        <div className="nm">{r.alias || <span className="noalias">{shortAddress(r.email)} · no alias</span>}</div>
        <div className="em">{r.email}</div>
        <div className="meta2">
          <span className="set-tag">{r.type}</span>
          {r.type === "imap" && <span>{r.host}:{r.port}{r.starttls ? " · STARTTLS" : ""}</span>}
          <Credentials c={credentialOf(r)} />
          {r.disabled && <span>· disabled</span>}
        </div>
        <HealthLine h={h} email={r.email} again={again} />
      </div>
      <div className="acts">
        <TestResult t={tested} />
        <button className="set-mini" onClick={edit}>Edit</button>
        <button className="set-mini" onClick={() => void testConnection({ email: r.email }, setTested)}>Test</button>
        <button className={arming ? "set-mini danger ok" : "set-mini danger"} onClick={() => void remove()}>{arming ? "Confirm?" : "Remove"}</button>
      </div>
    </div>
  );
}

function Backup() {
  const { data } = useQuery({ queryKey: ["exportPlan"], queryFn: () => get("/api/export/plan") });
  return (
    <div className="set-form" id="exportBlock">
      <div className="lbl">Backup &amp; Export</div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, marginTop: 8 }}>
        <span id="exportPlan" className="set-help" style={{ margin: 0 }}>{data?.ok ? `${data.total.toLocaleString()} messages · ${data.bodies.toLocaleString()} with full bodies · ${data.groups.length} folders` : "Counting…"}</span>
        <a className="set-mini" href="/api/export" download title="Download a full local snapshot of your synced mail"><Icon name="download" /> Backup (.db)</a>
      </div>
      <div className="set-help">Portable per-message files: <code>python3 -m mailmind export --format eml --out DIR</code> (or <code>mbox</code>). All local · read-only · never opens IMAP.</div>
    </div>
  );
}

function Connections() {
  const qc = useQueryClient();
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: () => get("/api/accounts"), staleTime: 0, gcTime: 0 });
  const health = useQuery({ queryKey: ["accountHealth"], queryFn: () => get("/api/account-health"), staleTime: 0, gcTime: 0 });
  const { data: digest } = useDigest();
  const [editing, setEditing] = useState<string | "new" | null>(null);
  // Esc steps back one thing at a time: out of a field, out of the form, and only then out of Settings.
  const stepBack = useEffectEvent((e: KeyboardEvent) => {
    if (e.key !== "Escape") return;
    const at = e.target as HTMLElement;
    if (at.closest?.("input,textarea,select")) at.blur();
    else if (editing !== null) setEditing(null);
    else return;
    e.stopPropagation();
    e.preventDefault();
  });
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => stepBack(e);
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, []);
  const rows = accounts.data?.accounts ?? [];
  const again = () => {
    void qc.invalidateQueries({ queryKey: ["accounts"] });
    void qc.invalidateQueries({ queryKey: ["accountHealth"] });
  };
  const saved = (added: boolean) => {
    setEditing(null);
    again();
    void qc.invalidateQueries({ queryKey: ["digest"] });
    void qc.invalidateQueries({ queryKey: ["folders"] });
    if (added) void syncNow(qc);     // a new connection has no mail yet: pull it now, not at the next sync
  };
  const healthOf = (email: string) => health.data?.accounts?.find((a) => a.account === email);
  const statusOf = (email: string) => ((digest?.status ?? {}) as unknown as Record<string, { health?: string } | undefined>)[email]?.health ?? "";
  let body: React.ReactNode;
  if (editing !== null) {
    body = <ConnectionForm key={editing} row={editing === "new" ? null : rows.find((r) => r.email === editing) ?? null} signatureDefault={accounts.data?.signature_default ?? ""} done={saved} cancel={() => setEditing(null)} />;
  } else if (accounts.isError) body = <div className="nores" style={{ padding: 30 }}>Couldn’t load your connections.</div>;
  else if (accounts.isPending) body = <div className="nores" role="status" style={{ padding: 30 }}>Loading…</div>;
  else {
    body = (
      <>
        <Backup />
        {!rows.length && <div className="nores" style={{ padding: 36 }}>No email connections yet.<br />Add one to get started.</div>}
        {rows.map((r) => <ConnectionRow key={r.email} r={r} h={healthOf(r.email)} status={statusOf(r.email)} edit={() => setEditing(r.email)} again={again} />)}
      </>
    );
  }
  return (
    <>
      <div className="set-head">
        <div>
          <h3 id="setTitle">Email connections</h3>
          <div className="set-sub">Stored locally in mailmind.config.json · never leaves this machine</div>
        </div>
        <button className="x" id="setClose" aria-label="Close" onClick={() => settingsOpen.set(false)}>✕</button>
      </div>
      <div className="set-body" id="setList">{body}</div>
      <div className="set-foot">
        <span className="set-hint" id="setHint">{rows.length} connection{rows.length === 1 ? "" : "s"}</span>
        <button className="set-add" id="setAdd" onClick={() => setEditing("new")}>+ Add a connection</button>
      </div>
    </>
  );
}

export function Settings() {
  const open = settingsOpen.useValue();
  useLayer(open, () => settingsOpen.set(false));
  return (
    <ModalOverlay className="settings show" isOpen={open} isDismissable onOpenChange={settingsOpen.set}>
      <Modal className="modal-through">
        <Dialog aria-labelledby="setTitle" className="set-box"><Connections /></Dialog>
      </Modal>
    </ModalOverlay>
  );
}
