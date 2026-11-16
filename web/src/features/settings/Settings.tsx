// Email connections: who MailMind reads, and how it reaches each. A password goes to the server once, which keeps it in the
// login Keychain, and never comes back; what the page shows of a stored one is only how it is kept. A new connection starts
// at its address, and which preset it takes is the server's to say (`/api/accounts/preset`): a provider's own address takes
// its settings, one whose mail a provider's servers take is offered them, and any other is Other IMAP, with its incoming and
// outgoing servers typed. A connection is tested as soon as it is saved, and a failed test says what to do next.
import { useCallback, useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Dialog, Modal, ModalOverlay, Tab, TabList, TabPanel, Tabs } from "react-aria-components";
import { EMPTY } from "../../lib/emptyWords";
import { get, post, refusalOf } from "../../lib/api/client";
import type { ArchiveFormat, GetAnswers, PostBodies } from "../../lib/api/types";
import { entries } from "../../lib/entries";
import { avatarHue, fileSize, relWhen, shortAddress } from "../../lib/text";
import { toast } from "../../lib/toast";
import { Icon } from "../../lib/ui/Icon";
import { useFocusFirst } from "../../lib/ui/menuKeys";
import { useDigest } from "../mailbox";
import { syncNow } from "../status";
import { settingsOpen, settingsSection, type SettingsSection } from "../../lib/settingsOpen";
import { AiForm } from "./Ai";
import { AI_KEPT } from "./aiChoices";
import { PreferencesForm } from "./Preferences";
import { outgoingBits, outgoingOf, type Security } from "./outgoing";
import { Seg } from "./Seg";
import { useStepBack } from "./useStepBack";
import { useLayer } from "../../lib/backTrap";

type Connection = NonNullable<GetAnswers["/api/accounts"]["accounts"]>[number];
type Health = GetAnswers["/api/account-health"]["accounts"][number];
type Credential = { set?: boolean; style?: string; ref?: string | string[]; var?: string; item?: string };
type Kind = "gmail" | "imap" | "proton";
type Cred = "keychain" | "op" | "env" | "proton_bridge";
type Link = { url: string; words: string };
type Tested = { kind: "run" | "ok" | "err"; text: string; detail?: string; link?: Link | null } | null;
type Found = GetAnswers["/api/accounts/preset"];

// Where a Google Account makes an App Password, which Gmail's IMAP takes in place of the account's own password.
const APP_PASSWORDS = "https://myaccount.google.com/apppasswords";
// How long the address rests before the server is asked which preset it takes.
const ASK_AFTER_MS = 400;

const credentialOf = (c: Connection) => c.credential as unknown as Credential;
const refOf = (c: Credential | null | undefined) => (Array.isArray(c?.ref) ? c.ref[0] : c?.ref) ?? "";

async function testConnection(payload: PostBodies["/api/accounts/test"], say: (t: Tested) => void) {
  say({ kind: "run", text: "Testing… (approve Touch ID if asked)" });
  try {
    const d = await post("/api/accounts/test", payload);
    if (d.connect) say({ kind: "ok", text: "✓ Connected" });
    // What to do next, the page that helps, and the class of what went wrong in small type, for the owner.
    else say({ kind: "err", text: `✕ ${d.says}`, detail: d.error === d.says ? undefined : d.error, link: d.link });
  } catch (e) {
    const no = refusalOf(e);
    say({ kind: "err", text: no ? `✕ ${no.detail || "failed"}` : "✕ test failed" });
  }
}

function TestResult({ t }: { t: Tested }) {
  return (
    <>
      <span className={t ? `set-status ${t.kind}` : "set-status"} role="status">{t?.text}</span>
      {t?.link && <a className="set-mini set-next" href={t.link.url} target="_blank" rel="noopener noreferrer">{t.link.words} <Icon name="external" /></a>}
      {t?.detail && <span className="set-class">{t.detail}</span>}
    </>
  );
}

// A preset's settings, as the server gave them, on the form's fields.
type Fields = { type: Kind; host: string; port: string; security: Security; smtpHost: string; smtpPort: string; smtpSecurity: Security };
function fieldsOf(settings: NonNullable<Found["settings"]>): Fields {
  return {
    type: settings.type as Kind,
    host: settings.host ?? "",
    port: String(settings.port ?? 993),
    security: settings.starttls ? "starttls" : "tls",
    smtpHost: settings.smtp_host ?? "",
    smtpPort: String(settings.smtp_port ?? 587),
    smtpSecurity: settings.smtp_starttls === false ? "tls" : "starttls",
  };
}

// What the server found of an address's domain, when a provider's servers take its mail and the settings in the form aren't
// its: the preset offered. With them in the form, a domain only said to fit.
function PresetOffer({ found, applied, use }: { found: Found | undefined; applied: boolean; use: () => void }) {
  if (!found?.preset || !found.settings || found.sure) return null;
  const whose = `${found.name} takes ${found.domain}’s mail`;
  if (applied) return <div className="set-help">{whose}: {found.name}’s settings fit it.</div>;
  return (
    <div className="set-offer" role="status">
      {whose}: use {found.name}’s settings? <button type="button" className="set-mini" onClick={use}>Use {found.name}</button>
    </div>
  );
}

// Where an incoming or outgoing server is: its host, port and how the connection is secured.
function ServerFields({ id, label, names, host, port, security, setHost, setPort, setSecurity, placeholder }: {
  id: string; label: string; names: [string, string]; host: string; port: string; security: Security; placeholder: string;
  setHost: (v: string) => void; setPort: (v: string) => void; setSecurity: (v: Security) => void;
}) {
  return (
    <div>
      <div className="lbl">{label}</div>
      <div className="twocol">
        <div><label className="lbl" htmlFor={`${id}Host`}>{names[0]}</label><input type="text" id={`${id}Host`} value={host} placeholder={placeholder} onChange={(e) => setHost(e.target.value)} /></div>
        <div><label className="lbl" htmlFor={`${id}Port`}>{names[1]}</label><input type="number" id={`${id}Port`} value={port} onChange={(e) => setPort(e.target.value)} /></div>
      </div>
      <Seg label={`${label} security`} value={security} options={[["tls", "TLS"], ["starttls", "STARTTLS"]]} onChange={setSecurity} />
    </div>
  );
}

// How to make the App Password Gmail asks for, with Google's page for it a click away.
function AppPasswordSteps() {
  return (
    <div className="set-apppw">
      <div className="set-help">Gmail takes an App Password here, not your Google password:</div>
      <ol>
        <li>Turn on 2-Step Verification for your Google Account.</li>
        <li>Create an App Password, named MailMind.</li>
        <li>Paste it above as Google shows it, spaces and all.</li>
      </ol>
      <a className="set-mini" href={APP_PASSWORDS} target="_blank" rel="noopener noreferrer">Open Google’s App Passwords <Icon name="external" /></a>
      <div className="set-help">No App Passwords there? Advanced Protection, or your Workspace admin, has switched them off.</div>
    </div>
  );
}

function ConnectionForm({ row, signatureDefault, done, cancel }: { row: Connection | null; signatureDefault: string; done: (email: string) => void; cancel: () => void }) {
  const was = row ? credentialOf(row) : null;
  const inside = useFocusFirst<HTMLDivElement>("input");
  const [alias, setAlias] = useState(row?.alias ?? "");
  const [signature, setSignature] = useState(row?.signature ?? "");
  const [email, setEmail] = useState(row?.email ?? "");
  // A new connection has no type until the server says which its address takes.
  const [type, setType] = useState<Kind | null>((row?.type as Kind | undefined) ?? null);
  const [host, setHost] = useState(row?.host ?? "");
  const [port, setPort] = useState(String(row?.port ?? 993));
  const [security, setSecurity] = useState<Security>(row?.starttls ? "starttls" : "tls");
  const outgoing = outgoingOf(row);
  const [smtpHost, setSmtpHost] = useState(outgoing.host);
  const [smtpPort, setSmtpPort] = useState(outgoing.port);
  const [smtpSecurity, setSmtpSecurity] = useState<Security>(outgoing.security);
  const [trash, setTrash] = useState<string | null>(null);
  const [applied, setApplied] = useState<string | null>(null);   // the preset whose settings are in the form
  const [style, setStyle] = useState<Exclude<Cred, "proton_bridge">>(was?.style === "op" || was?.style === "env" ? was.style : "keychain");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [opRef, setOpRef] = useState(was?.style === "op" ? refOf(was) : "");
  const [envVar, setEnvVar] = useState(was?.style === "env" ? was.var ?? "" : "");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [tested, setTested] = useState<Tested>(null);
  // The address as it last rested, which the server is asked about; a saved connection's is fixed, and asks nothing.
  const [asked, setAsked] = useState("");
  useEffect(() => {
    if (row) return;
    const typed = email.trim();
    const t = setTimeout(() => setAsked(typed.includes("@") ? typed : ""), ASK_AFTER_MS);
    return () => clearTimeout(t);
  }, [row, email]);
  const found = useQuery({
    queryKey: ["preset", asked],
    queryFn: () => get(`/api/accounts/preset?email=${encodeURIComponent(asked)}`),
    enabled: asked !== "",
    staleTime: Infinity,
  });
  const answer = asked ? found.data : undefined;
  const use = (settings: NonNullable<Found["settings"]>, preset: string) => {
    const f = fieldsOf(settings);
    setType(f.type);
    if (f.type === "imap") {
      setHost(f.host); setPort(f.port); setSecurity(f.security);
      setSmtpHost(f.smtpHost); setSmtpPort(f.smtpPort); setSmtpSecurity(f.smtpSecurity);
    }
    setTrash(settings.trash ?? null);
    setApplied(preset);
  };
  // The answer picks the type, once for each address asked about: a provider's own address takes its settings, and any
  // other is Other IMAP, with what a preset filled in before taken out again (what was typed stays). Settled as the answer
  // is rendered, as state kept from the render before.
  const [answered, setAnswered] = useState<Found | undefined>(undefined);
  if (!row && answer && answer !== answered) {
    setAnswered(answer);
    if (answer.sure && answer.settings && answer.preset) use(answer.settings, answer.preset);
    else {
      if (applied !== null) {
        setHost(""); setPort("993"); setSecurity("tls");
        setSmtpHost(""); setSmtpPort("587"); setSmtpSecurity("starttls");
      }
      if (applied !== null || type === null) setType("imap");
      setApplied(null);
      setTrash(null);
    }
  }
  const unsupported = !row && answer?.supported === false;   // a provider MailMind can't log in to yet: no password asked
  const note = row ? undefined : answer?.note;
  const link = row ? undefined : (answer?.preset && answer.preset === applied ? answer.link : undefined);

  const credential = () => {
    if (type === "proton") return { style: "proton_bridge" };
    if (style === "keychain") return password.trim() ? { style: "keychain", value: password.trim() } : null;
    if (style === "op") return opRef.trim() ? { style: "op", ref: opRef.trim() } : null;
    return envVar.trim() ? { style: "env", var: envVar.trim() } : null;
  };
  const kind: Kind = type ?? "imap";
  const imapBits = () => (kind === "imap"
    ? { host: host.trim(), port: Number(port) || 993, starttls: security === "starttls",
        ...outgoingBits({ host: smtpHost, port: smtpPort, security: smtpSecurity }, row) }
    : {});
  // A Proton account logs in with the Bridge's password, which only a saved account is tested with.
  const testsOnceAdded = !row && kind === "proton";
  const test = () => {
    const c = credential();
    if (kind === "proton") { if (row) void testConnection({ email: row.email }, setTested); }
    else if (c) void testConnection({ draft: { email: row ? row.email : email.trim(), type: kind, ...imapBits(), credential: c } }, setTested);
    else if (row) void testConnection({ email: row.email }, setTested);
    else setTested({ kind: "err", text: "enter a password to test" });
  };
  const save = async () => {
    setError("");
    const c = credential(), address = row ? row.email : email.trim();
    if (!row && !address?.includes("@")) return setError("A valid email is required.");
    if (unsupported) return setError(note ?? "MailMind can’t log in to this account yet.");
    if (kind === "imap" && !host.trim()) return setError("A host is required for an Other IMAP account.");
    if (!row && !c) return setError("Set a password (or 1Password ref / env var).");
    setSaving(true);
    try {
      await post(row ? "/api/accounts/edit" : "/api/accounts/add", { alias: alias.trim(), signature: signature.trim(), type: kind, email: address, ...imapBits(), ...(trash && !row ? { trash } : {}), ...(c ? { credential: c } : {}) });
      toast(`${row ? "Updated" : "Added"} ${shortAddress(address)}`);
      done(address ?? "");
    } catch (e) {
      const gone = refusalOf(e)?.status === 404;
      setError(gone ? refusalOf(e)?.detail || "Could not save." : "Save failed.");
      setSaving(false);
    }
  };
  return (
    <div className="set-form" ref={inside}>
      <label className="lbl" htmlFor="setEmail">Email</label>
      <input type="email" id="setEmail" value={email} readOnly={Boolean(row)} placeholder="you@example.com" onChange={(e) => setEmail(e.target.value)} />
      {row && <div className="set-help">Identity is fixed — remove and re-add to change the address.</div>}
      {type && !unsupported && (
        <>
          <div className="lbl">Type</div>
          <Seg label="Type" value={type} options={[["gmail", "Gmail"], ["imap", "Other IMAP"], ["proton", "Proton"]]} onChange={(t) => { setType(t); setApplied(null); setTrash(null); }} />
        </>
      )}
      {!row && answer?.sure && answer.name && !unsupported && <div className="set-help">{answer.name} address{applied === answer.preset ? `: ${answer.name}’s settings are filled in.` : "."}</div>}
      {!row && <PresetOffer found={answer} applied={Boolean(answer?.preset && applied === answer.preset)} use={() => answer?.settings && answer.preset && use(answer.settings, answer.preset)} />}
      {note && <div className="set-note" role="note">{note}</div>}
      {kind === "imap" && type && !unsupported && (
        <>
          <ServerFields id="set" label="Incoming server" names={["Host", "Port"]} host={host} port={port} security={security} placeholder="imap.yourhost.com" setHost={setHost} setPort={setPort} setSecurity={setSecurity} />
          <ServerFields id="setSmtp" label="Outgoing server (SMTP)" names={["Outgoing host", "Outgoing port"]} host={smtpHost} port={smtpPort} security={smtpSecurity} placeholder={host.trim() || "smtp.yourhost.com"} setHost={setSmtpHost} setPort={setSmtpPort} setSecurity={setSmtpSecurity} />
        </>
      )}
      {!unsupported && <div className="lbl">Password</div>}
      {unsupported ? null : kind === "proton"
        ? (
          <>
            <Seg label="Credential" value="proton_bridge" options={[["proton_bridge", "Proton Bridge"]]} onChange={() => {}} />
            <div className="set-help">Uses the password Proton Bridge shows for this address, kept in your login Keychain.</div>
            {testsOnceAdded && <div className="set-help">Add the account, then test it.</div>}
            {link && <a className="set-mini set-next" href={link.url} target="_blank" rel="noopener noreferrer">{link.words} <Icon name="external" /></a>}
          </>
        )
        : (
          <>
            <Seg label="Credential" value={style} options={[["keychain", "Password"], ["op", "1Password"], ["env", "Env var"]]} onChange={setStyle} />
            {style === "keychain" && (
              <>
                <div className="pwwrap">
                  <input type={showPassword ? "text" : "password"} aria-label="Password" value={password} autoComplete="new-password" placeholder={row ? "•••••••• unchanged — leave blank to keep" : kind === "gmail" ? "abcd efgh ijkl mnop" : "paste the password / app password"} onChange={(e) => setPassword(e.target.value)} />
                  <button type="button" className="eye" aria-label={showPassword ? "Hide password" : "Show password"} onClick={() => setShowPassword(!showPassword)}><Icon name="eye" /></button>
                </div>
                <div className="set-help">Kept in your login Keychain, never in a file.</div>
                {link && kind !== "gmail" && <a className="set-mini set-next" href={link.url} target="_blank" rel="noopener noreferrer">{link.words} <Icon name="external" /></a>}
                {kind === "gmail" && <AppPasswordSteps />}
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
      <label className="lbl" htmlFor="setAlias">Alias (optional)</label>
      <input type="text" id="setAlias" value={alias} placeholder="Friendly name shown on the dots & rows" onChange={(e) => setAlias(e.target.value)} />
      <label className="lbl" htmlFor="setSig">Signature (optional)</label>
      <textarea id="setSig" rows={3} className="set-sig" value={signature} placeholder={`Appended to new mail and above quoted replies${signatureDefault ? " — blank uses the global default" : ""}`} onChange={(e) => setSignature(e.target.value)} />
      <div className="err" role="alert">{error}</div>
      <div className="formbar">
        <button className="set-mini" disabled={unsupported || testsOnceAdded} title={testsOnceAdded ? "Add the account, then test it." : undefined} onClick={test}>Test connection</button>
        <button className="abtn" style={{ marginLeft: "auto" }} onClick={cancel}>Cancel</button>
        <button className="abtn prim" disabled={saving || unsupported} onClick={() => void save()}>{saving ? "Saving…" : "Save connection"}</button>
      </div>
      <div className="set-tested"><TestResult t={tested} /></div>
    </div>
  );
}

function Credentials({ c }: { c: Credential | null }) {
  if (!c?.set) return <span className="set-cred">no password set</span>;
  if (c.style === "keychain") return <span className="set-cred"><span className="lock"><Icon name="lock" /></span> in the Keychain</span>;
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

type Live = { in_inbox?: number | null; error?: string | null };   // the generated type only knows the answer of a world where nothing failed

function ConnectionRow({ r, h, live, status, edit, again, testNow, testing }: { r: Connection; h: Health | undefined; live: Live | undefined; status: string; edit: () => void; again: () => void; testNow: boolean; testing: () => void }) {
  const qc = useQueryClient();
  const [tested, setTested] = useState<Tested>(null);
  useEffect(() => {
    if (!testNow) return;
    testing();
    void testConnection({ email: r.email }, setTested);
  }, [testNow, testing, r.email]);
  const [arming, setArming] = useState(false);
  const [deleteMail, setDeleteMail] = useState(true);   // what this Mac keeps of its mail goes too, unless unticked
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
    const t = setTimeout(() => setArming(false), 3000);   // a tick of the box asks again from the start
    return () => clearTimeout(t);
  }, [arming, deleteMail]);
  const remove = async () => {
    if (!arming) return setArming(true);
    setArming(false);
    try {
      await post("/api/accounts/delete", { email: r.email, delete_mail: deleteMail });
      toast(deleteMail ? `Removed ${shortAddress(r.email)} and its mail on this Mac` : `Removed ${shortAddress(r.email)}`);
      again();
      void qc.invalidateQueries({ queryKey: ["digest"] });
    } catch (e) {
      const gone = refusalOf(e)?.status === 404;
      toast(gone ? refusalOf(e)?.detail || "Couldn’t remove" : "Remove failed");
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
        <HealthLine h={h} email={r.email ?? ""} again={again} />
        <div className="set-tested"><TestResult t={tested} /></div>
        {arming && (
          <div className="set-remove">
            <label><input type="checkbox" checked={deleteMail} onChange={(e) => setDeleteMail(e.target.checked)} /> Also delete its mail from this Mac</label>
            <div className="set-help">Its mail on the server stays as it is.</div>
          </div>
        )}
      </div>
      <div className="acts">
        {live?.error
          ? <span className="set-count" style={{ color: "var(--err)" }}>{live.error.slice(0, 18)}</span>
          : live?.in_inbox != null && <span className="set-count" style={{ color: "var(--mute)" }}>{live.in_inbox}</span>}
        <button className="set-mini" onClick={edit}>Edit</button>
        <button className="set-mini" onClick={() => void testConnection({ email: r.email }, setTested)}>Test</button>
        <button className={arming ? "set-mini danger ok" : "set-mini danger"} onClick={() => void remove()}>{arming ? "Confirm?" : "Remove"}</button>
      </div>
    </div>
  );
}

const FORMAT_LABEL: Record<ArchiveFormat, string> = { eml: ".eml files", mbox: "mbox" };

function Backup() {
  const { data } = useQuery({ queryKey: ["exportPlan"], queryFn: () => get("/api/export/plan") });
  const [format, setFormat] = useState<ArchiveFormat>("eml");
  return (
    <div className="set-form" id="exportBlock">
      <div className="lbl">Backup &amp; Export</div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, marginTop: 8 }}>
        <span id="exportPlan" className="set-help" style={{ margin: 0 }}>{data ? `${data.total.toLocaleString()} messages · ${data.bodies.toLocaleString()} with full bodies · ${data.groups.length} folders · ${fileSize(data.database_bytes)} on this Mac` : "Counting…"}</span>
        <a className="set-mini" href="/api/export" download title="Download a full local snapshot of your synced mail, encrypted as the database is: it opens with the key in this Mac's Keychain"><Icon name="download" /> Backup (.db)</a>
      </div>
      <div className="pref-line">
        <Seg label="Export format" value={format} options={entries(FORMAT_LABEL)} onChange={setFormat} />
        <a className="set-mini" href={`/api/export/archive?format=${format}`} download title="Download your synced mail as a zip any mail program can read"><Icon name="download" /> Export (.zip)</a>
      </div>
      <div className="set-help">The backup is MailMind’s own format. The zip is your mail for any other mail program: one .eml file a message, or one mbox a folder. A message with no full body stored comes as its headers. All local · read-only · never opens IMAP.</div>
    </div>
  );
}

function Connections() {
  const qc = useQueryClient();
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: () => get("/api/accounts"), staleTime: 0, gcTime: 0 });
  const health = useQuery({ queryKey: ["accountHealth"], queryFn: () => get("/api/account-health"), staleTime: 0, gcTime: 0 });
  const stats = useQuery({ queryKey: ["stats"], queryFn: () => get("/api/stats") });
  const { data: digest } = useDigest();
  const [editing, setEditing] = useState<string | "new" | null>(null);
  const [testNext, setTestNext] = useState<string | null>(null);   // the connection just saved, tested as its row shows
  const tested = useCallback(() => setTestNext(null), []);
  useStepBack(() => {
    if (editing === null) return false;
    setEditing(null);
    return true;
  });
  const rows = accounts.data?.accounts ?? [];
  const again = () => {
    void qc.invalidateQueries({ queryKey: ["accounts"] });
    void qc.invalidateQueries({ queryKey: ["accountHealth"] });
    void qc.invalidateQueries({ queryKey: ["stats"] });
  };
  const saved = (email: string) => {
    const added = editing === "new";
    setEditing(null);
    setTestNext(email);
    again();
    void qc.invalidateQueries({ queryKey: ["digest"] });
    void qc.invalidateQueries({ queryKey: ["folders"] });
    if (added) void syncNow(qc);     // a new connection has no mail yet: pull it now, not at the next sync
  };
  const healthOf = (email: string) => health.data?.accounts?.find((a) => a.account === email);
  const liveOf = (email: string) => stats.data?.accounts?.find((a) => a.account === email) as unknown as Live | undefined;
  const statusOf = (email: string) => (liveOf(email)?.error ? "error" : ((digest?.status ?? {}) as unknown as Record<string, { health?: string } | undefined>)[email]?.health ?? "");
  let body: React.ReactNode;
  if (editing !== null) {
    body = <ConnectionForm key={editing} row={editing === "new" ? null : rows.find((r) => r.email === editing) ?? null} signatureDefault={accounts.data?.signature_default ?? ""} done={saved} cancel={() => setEditing(null)} />;
  } else if (accounts.isError) body = <div className="nores" style={{ padding: 30 }}>Couldn’t load your connections.</div>;
  else if (accounts.isPending) body = <div className="nores" role="status" style={{ padding: 30 }}>Loading…</div>;
  else {
    body = (
      <>
        <Backup />
        {!rows.length && <div className="nores" style={{ padding: 36 }}>{EMPTY.connections}<br />Add one to get started.</div>}
        {rows.map((r) => <ConnectionRow key={r.email} r={r} h={healthOf(r.email ?? "")} live={liveOf(r.email ?? "")} status={statusOf(r.email ?? "")} edit={() => setEditing(r.email)} again={again} testNow={r.email === testNext} testing={tested} />)}
      </>
    );
  }
  return (
    <>
      <div className="set-body" id="setList">{body}</div>
      <div className="set-foot">
        <span className="set-hint" id="setHint">{rows.length} connection{rows.length === 1 ? "" : "s"}</span>
        <button className="set-add" id="setAdd" onClick={() => setEditing("new")}>+ Add a connection</button>
      </div>
    </>
  );
}

const SECTION_TITLE: Record<SettingsSection, string> = { connections: "Email connections", preferences: "Preferences", ai: "AI" };
const KEPT_IN_THE_FILE = "Stored locally in mailmind.config.json · never leaves this machine";

function Home() {
  const section = settingsSection.useValue();
  return (
    <Tabs className="set-home" selectedKey={section} onSelectionChange={(k) => settingsSection.set(k as SettingsSection)}>
      <div className="set-head">
        <div>
          <h3 id="setTitle">{SECTION_TITLE[section]}</h3>
          <div className="set-sub" id="setSub">{section === "ai" ? AI_KEPT : KEPT_IN_THE_FILE}</div>
        </div>
        <button className="x" id="setClose" aria-label="Close" onClick={() => settingsOpen.set(false)}>✕</button>
      </div>
      <TabList className="set-tabs" aria-label="Settings">
        <Tab id="connections" className="set-tab">Connections</Tab>
        <Tab id="preferences" className="set-tab">Preferences</Tab>
        <Tab id="ai" className="set-tab">AI</Tab>
      </TabList>
      <TabPanel id="connections" className="set-panel"><Connections /></TabPanel>
      <TabPanel id="preferences" className="set-panel"><PreferencesForm /></TabPanel>
      <TabPanel id="ai" className="set-panel"><AiForm /></TabPanel>
    </Tabs>
  );
}

export function Settings() {
  const open = settingsOpen.useValue();
  useLayer(open, () => settingsOpen.set(false));
  return (
    <ModalOverlay className="settings show" isOpen={open} isDismissable onOpenChange={settingsOpen.set}>
      <Modal className="modal-through">
        <Dialog aria-labelledby="setTitle" className="set-box"><Home /></Dialog>
      </Modal>
    </ModalOverlay>
  );
}
