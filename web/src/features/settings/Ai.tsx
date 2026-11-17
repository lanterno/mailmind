// AI (#49): AI mode, whether MailMind uses it at all; and once it is on, the connection (which provider, with what key, model
// and URL), what it may spend and what it writes ahead. Each choice saves on its own, as Preferences' do, and the digest is
// read again so the page shows what is now in force. A key goes to the server once, which keeps it in the login Keychain, and
// never comes back: the page says only whether one is kept. The provider is asked for its models only while this tab is open
// with AI mode on, and again as a person acts here (chooses it, keeps a key, gives a URL, asks again), never on its own. For a
// model on this Mac, the servers running one here are looked for in its place, as it is picked and when asked to look again,
// and picking one of their models sets the server's URL and the model together.
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Input, Label, NumberField } from "react-aria-components";
import { aiSends, aiStanding, draftAheadSends, PROVIDER_LABEL, PROVIDER_NAME, sendsOf } from "../../lib/ai";
import { get, post, refusalOf } from "../../lib/api/client";
import { LIMITS } from "../../lib/api/limits.gen";
import type { AiProvider } from "../../lib/api/types";
import { entries } from "../../lib/entries";
import { prefs, setPrefs } from "../../lib/prefs";
import { createStore } from "../../lib/store";
import { toast } from "../../lib/toast";
import { Icon } from "../../lib/ui/Icon";
import { useDigest } from "../mailbox";
import { aiParts, BILLING, billOfRecord, CHOSEN_ELSEWHERE, CONTEXT_STEP, GET_OLLAMA, keepKey, localFound, localMenu, localPicked, modelChoices, modelsUsed, NEEDS, providerChanges, spentOver, SUGGESTED_MODEL, summariesWaiting, urlToSave } from "./aiChoices";
import { Check } from "./Check";
import { useStepBack } from "./useStepBack";

// The key for the provider chosen: kept or not, a field to keep one, and Remove for one kept.
function KeyField({ provider, kept, keptFrom, done }: { provider: AiProvider; kept: boolean; keptFrom: string; done: (kept: AiProvider[]) => void }) {
  const [field] = useState(() => createStore(""));
  const typed = field.useValue();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [refused, setRefused] = useState("");
  const needs = NEEDS[provider], billing = BILLING[provider];
  const keep = async () => {
    setRefused("");
    setBusy(true);
    try {
      const d = await keepKey(field, provider, (body) => post("/api/ai/key", body));
      if (d) {
        done(d.kept);
        toast(`Key kept for ${PROVIDER_NAME[provider]}`);
      }
    } catch (e) {
      setRefused(refusalOf(e)?.detail || "Couldn’t keep the key.");
    }
    setBusy(false);
  };
  const remove = async () => {
    setRefused("");
    try {
      done((await post("/api/ai/key/clear", { provider })).kept);
      input.current?.focus();     // Remove goes with the key: the field to paste another stays
      toast(`Key removed for ${PROVIDER_NAME[provider]}`);
    } catch {
      setRefused("Couldn’t remove the key.");
    }
  };
  return (
    <>
      <label className="lbl" htmlFor="aiKey">API key{needs.key === "optional" ? " (optional)" : ""}</label>
      {kept && (
        <div className="pref-line">
          <span className="set-cred"><span className="lock"><Icon name="lock" /></span> A key is kept in the Keychain</span>
          <button type="button" className="set-mini danger" onClick={() => void remove()}>Remove</button>
        </div>
      )}
      <div className="ai-row">
        <input
          ref={input}
          type="password"
          id="aiKey"
          value={typed}
          autoComplete="off"
          spellCheck={false}
          placeholder={kept ? "Paste a new key to replace it" : "Paste your key"}
          onChange={(e) => field.set(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") void keep(); }}
        />
        <button type="button" className="set-mini" disabled={busy || !typed.trim()} onClick={() => void keep()}>{busy ? "Keeping…" : "Keep key"}</button>
      </div>
      <div className="set-help">
        {needs.key === "optional" ? `Only if your ${provider === "local" ? "server" : "service"} asks for one. ` : ""}Kept in your login Keychain, never in a file.
        {!kept && keptFrom ? ` MailMind is using the key in ${keptFrom} until you keep one here.` : ""}
      </div>
      {needs.keysAt && (
        <a className="set-mini set-link" href={needs.keysAt} target="_blank" rel="noopener noreferrer">
          Make a key at {needs.keysAt.replace(/^https:\/\//, "")} <Icon name="external" />
        </a>
      )}
      {billing && <div className="set-help" id="aiBilling">{billing.words}</div>}
      {billing?.creditAt && (
        <a className="set-mini set-link" href={billing.creditAt} target="_blank" rel="noopener noreferrer">
          Add credit at {new URL(billing.creditAt).host} <Icon name="external" />
        </a>
      )}
      <div className="err" role="alert">{refused}</div>
    </>
  );
}

// A model's name as the provider writes it, for one its list doesn't offer. It opens on Type a name, which goes as it does, so
// the focus comes to the field.
function ModelByHand({ chosen, use, back }: { chosen: string; use: (model: string) => void; back: () => void }) {
  const [typed, setTyped] = useState(chosen);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => input.current?.focus(), []);
  return (
    <div className="ai-row">
      <input
        ref={input}
        type="text"
        id="aiModel"
        value={typed}
        maxLength={LIMITS.ai_model_chars}
        autoComplete="off"
        spellCheck={false}
        placeholder="The model’s name, as the provider writes it"
        onChange={(e) => setTyped(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") use(typed.trim()); }}
      />
      <button type="button" className="set-mini" onClick={() => use(typed.trim())}>Use it</button>
      <button type="button" className="set-mini" onClick={back}>Pick from the list</button>
    </div>
  );
}

// The service's URL, filled in with the provider's own (for a model on this Mac, where Ollama answers): saved as a person
// leaves it, "" where it is the provider's own. It shows the URL saved as that changes (saved here, put back by the server,
// or set by a model picked) and stays the same field, so the focus stays in it.
function UrlField({ saved, prefill, save }: { saved: string; prefill: string; save: (url: string) => void }) {
  const [typed, setTyped] = useState(saved || prefill);
  const [shown, setShown] = useState(saved);
  if (saved !== shown) {
    setShown(saved);
    setTyped(saved || prefill);
  }
  const commit = () => {
    const url = urlToSave(typed, prefill);
    if (url !== saved) save(url);
  };
  return (
    <input
      type="text"
      id="aiUrl"
      value={typed}
      maxLength={LIMITS.ai_base_url_chars}
      autoComplete="off"
      spellCheck={false}
      placeholder="https://"
      onChange={(e) => setTyped(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => { if (e.key === "Enter") commit(); }}
    />
  );
}

// What AI cost over the last week and this month, by its main features, as the server counts it: read each time the tab opens.
function SpentLately({ provider }: { provider: AiProvider }) {
  const spent = useQuery({
    queryKey: ["aiSpending"],
    queryFn: () => get("/api/ai/spending"),
    staleTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
  });
  if (!spent.data) return null;
  return (
    <div id="aiSpent">
      <div className="set-help">{spentOver("The last 7 days", spent.data.week)}</div>
      <div className="set-help">{spentOver("This month", spent.data.month)}</div>
      <div className="set-help">{billOfRecord(provider)}</div>
    </div>
  );
}

export function AiForm() {
  const p = prefs.useValue();
  const qc = useQueryClient();
  const { data: digest } = useDigest();
  useStepBack();
  const provider = p.ai_provider, needs = NEEDS[provider], name = PROVIDER_NAME[provider], local = provider === "local";
  const [byHand, setByHand] = useState(false);
  // The model menu, which the focus goes back to as a model typed is used or the list is picked from again.
  const menuRef = useRef<HTMLSelectElement>(null);
  const backToMenu = useRef(false);
  useEffect(() => {
    if (byHand || !backToMenu.current) return;
    backToMenu.current = false;
    menuRef.current?.focus();
  }, [byHand]);
  // The provider and URL the server holds, which its models are asked by: a choice counts once it is saved.
  const [held, setHeld] = useState({ provider, url: p.ai_base_url });
  const keys = useQuery({ queryKey: ["aiKeys"], queryFn: () => get("/api/ai/key") });
  const kept = keys.data?.kept.includes(provider) ?? false;
  const ai = digest?.ai;
  const keptFrom = ai?.provider === provider && ai.key_from && ai.key_from !== "mailmind.config.json" ? ai.key_from : "";
  const current = held.provider === provider;
  // A URL counts once the server holds it: asked before then, it would say to give one.
  const wants = needs.key === "needed" && !kept && !keptFrom ? "key" : needs.url === "" && !(current ? held.url : p.ai_base_url) ? "url" : null;
  const models = useQuery({
    queryKey: ["aiModels", held.provider, held.url, kept],
    queryFn: () => get(`/api/ai/models?provider=${held.provider}`),
    enabled: current && !local && keys.data !== undefined && wants === null && p.ai_enabled,
    staleTime: Infinity,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  // The model servers running on this Mac, looked for as a model on this Mac is picked or shown, and on Look again.
  const found = useQuery({
    queryKey: ["aiLocal"],
    queryFn: () => get("/api/ai/local"),
    enabled: local && p.ai_enabled,
    staleTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  const inForce = () => void qc.invalidateQueries({ queryKey: ["digest"] });
  const choose = (next: AiProvider) => {
    if (next === provider) return;
    setByHand(false);
    setPrefs(providerChanges(next), () => {
      setHeld({ provider: next, url: "" });
      inForce();
    });
  };
  const chooseUrl = (ai_base_url: string) => {
    setPrefs({ ai_base_url }, () => {
      setHeld({ provider, url: ai_base_url });
      inForce();
    });
  };
  // A key kept, replaced or removed: the provider is asked for its models again with the key it holds now, since a list or a
  // refusal for the key before says nothing of this one. Kept or removed, the listing's own key changes, and it is asked as
  // that does wherever it can be; replaced, its key stays the same, so it is asked again here.
  const keysKept = (now: AiProvider[]) => {
    const replaced = kept && now.includes(provider);
    qc.setQueryData(["aiKeys"], { ok: true, kept: now });
    void qc.invalidateQueries({ queryKey: ["aiModels"], refetchType: replaced ? "active" : "none" });
    inForce();
  };
  const chooseModel = (ai_model: string) => {
    setByHand(false);
    if (ai_model !== p.ai_model) setPrefs({ ai_model }, inForce);
  };
  const askAgain = () => {
    void models.refetch();
  };
  const chooseLocal = (value: string) => {
    if (!value || value === CHOSEN_ELSEWHERE) return;
    setByHand(false);
    const changes = localPicked(value);
    setPrefs(changes, () => {
      setHeld({ provider, url: changes.ai_base_url ?? "" });
      inForce();
    });
  };
  const menu = localMenu(found.data?.servers ?? [], p.ai_base_url, p.ai_model);
  const noneFound = found.data?.servers.length === 0;     // no model server here yet: the steps to get one
  let lookedFor = "";
  if (found.isFetching) lookedFor = "Looking for model servers on this Mac…";
  else if (found.isError) lookedFor = refusalOf(found.error)?.detail || "Couldn’t look for model servers on this Mac.";
  else if (found.data) lookedFor = localFound(found.data.servers);
  const listedNow = current ? models.data : undefined;
  const own = listedNow ? listedNow.default : needs.ownModel ? undefined : null;
  const choices = modelChoices(listedNow?.models ?? [], own, p.ai_model);
  let listed = "";
  if (current && models.isFetching) listed = `Asking ${name} for its models…`;
  else if (current && models.isError) listed = refusalOf(models.error)?.detail || "Couldn’t reach the provider.";
  else if (!listedNow && wants === "key") listed = `Keep a key for ${name}, and its models are listed here.`;
  else if (!listedNow && wants === "url") listed = "Give the service’s URL, and its models are listed here.";
  else if (!needs.ownModel && !p.ai_model) listed = `${name.charAt(0).toUpperCase()}${name.slice(1)} has no model of its own: choose one.`;
  const parts = new Set(aiParts(p.ai_enabled));
  const sends = sendsOf(ai, p, provider);
  const ahead = draftAheadSends(sends);
  return (
    <div className="set-body" id="aiBody">
      <div className="set-form">
        <Check checked={p.ai_enabled} onChange={(ai_enabled) => setPrefs({ ai_enabled }, inForce)}>AI mode</Check>
        <div className="set-help">Summaries, the brief, drafts and Ask. Off, MailMind sends nothing to any AI service.</div>
        <div className="set-help" id="aiSends">{aiSends(sends)}</div>
      </div>
      {parts.has("connection") && (
        <div className="set-form" id="aiConnection">
          <label className="lbl" htmlFor="aiProvider">Provider</label>
          <select id="aiProvider" value={provider} onChange={(e) => choose(e.target.value as AiProvider)}>
            {entries(PROVIDER_LABEL).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
          </select>
          {!local && needs.key !== "none" && <KeyField key={provider} provider={provider} kept={kept} keptFrom={keptFrom} done={keysKept} />}
          <label className="lbl" htmlFor="aiModel">Model</label>
          {byHand && (
            <ModelByHand
              chosen={p.ai_model}
              use={(model) => {
                backToMenu.current = true;
                chooseModel(model);
              }}
              back={() => {
                backToMenu.current = true;
                setByHand(false);
              }}
            />
          )}
          {!byHand && local && (
            <div className="ai-row">
              <select ref={menuRef} id="aiModel" value={menu.value} onChange={(e) => chooseLocal(e.target.value)}>
                {menu.first && <option value={menu.first.value}>{menu.first.label}</option>}
                {menu.groups.map((g) => (
                  <optgroup key={g.url} label={g.name}>
                    {g.models.length
                      ? g.models.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)
                      : <option value={`none ${g.url}`} disabled>No model to chat with yet</option>}
                  </optgroup>
                ))}
              </select>
              <button type="button" className="set-mini" disabled={found.isFetching} onClick={() => void found.refetch()}>Look again</button>
              <button type="button" className="set-mini" onClick={() => setByHand(true)}>Type a name</button>
            </div>
          )}
          {!byHand && !local && (
            <div className="ai-row">
              <select ref={menuRef} id="aiModel" value={p.ai_model} onChange={(e) => chooseModel(e.target.value)}>
                {choices.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
              </select>
              <button type="button" className="set-mini" aria-label="Ask the provider for its models again" title="Ask the provider for its models again" disabled={current && models.isFetching} onClick={askAgain}><Icon name="refresh" /></button>
              <button type="button" className="set-mini" onClick={() => setByHand(true)}>Type a name</button>
            </div>
          )}
          <div className="set-help" role="status">{local ? lookedFor : listed}</div>
          {local && noneFound && (
            <div className="set-apppw" id="aiGetOllama">
              <ol>
                <li><a className="set-mini" href={GET_OLLAMA} target="_blank" rel="noopener noreferrer">Get Ollama <Icon name="external" /></a> and open it.</li>
                <li>{SUGGESTED_MODEL}</li>
                <li>{CONTEXT_STEP}</li>
                <li>Then look again.</li>
              </ol>
            </div>
          )}
          {needs.url !== null && (
            <>
              <label className="lbl" htmlFor="aiUrl">{local ? "Your model server’s URL" : "The service’s URL"}</label>
              <UrlField key={provider} saved={p.ai_base_url} prefill={needs.url} save={chooseUrl} />
              <div className="set-help">
                {local
                  ? "On this Mac only: 127.0.0.1, ::1 or localhost, up to /v1. Picking a model above fills it in."
                  : "Where the service answers, up to /v1: https, or http on this Mac."}
              </div>
            </>
          )}
          {local && (
            <>
              <KeyField key={provider} provider={provider} kept={kept} keptFrom={keptFrom} done={keysKept} />
              {!noneFound && <div className="set-help" id="aiContext">{CONTEXT_STEP}</div>}
            </>
          )}
          <div className="set-help ai-standing" id="aiStanding" role="status">{aiStanding(ai)}</div>
        </div>
      )}
      {parts.has("spending") && (
        <div className="set-form">
          <div className="lbl">Spending</div>
          <NumberField
            className="pref-line"
            minValue={0.01}
            maxValue={LIMITS.ai_daily_limit_usd}
            step={0.01}
            formatOptions={{ maximumFractionDigits: 2 }}
            value={p.ai_daily_limit_usd}
            onChange={(ai_daily_limit_usd) => {
              if (Number.isFinite(ai_daily_limit_usd)) setPrefs({ ai_daily_limit_usd }, inForce);
            }}
          >
            <Label className="pref-name">Daily limit, in US dollars</Label>
            <Input className="pref-num" id="prefAiLimit" />
          </NumberField>
          <div className="set-help">Once a day’s AI has cost this much, background work stops and MailMind asks before spending more.</div>
          <div className="set-help" id="aiModelsUsed">{modelsUsed(ai)}</div>
          <div className="set-help" id="aiWaiting">{summariesWaiting(ai?.summaries_waiting)}</div>
          <SpentLately provider={provider} />
        </div>
      )}
      {parts.has("ahead") && (
        <div className="set-form">
          <div className="lbl">Written ahead</div>
          <Check checked={p.morning_brief} onChange={(morning_brief) => setPrefs({ morning_brief })}>Have today’s brief ready each morning</Check>
          <div className="set-help">Written before you open your Inbox, within the daily limit, and only if you’ve used MailMind in the last two days.</div>
          <Check checked={p.weekly_review} onChange={(weekly_review) => setPrefs({ weekly_review })}>Have last week’s review ready each Monday</Check>
          <div className="set-help">Written on Monday morning, within the daily limit, and only if you’ve used MailMind in the last two days. Your week shows it until the next Monday, and ↻ there writes a new one.</div>
          <Check checked={p.auto_tidy} onChange={(auto_tidy) => setPrefs({ auto_tidy })}>File low-priority mail by itself, once a day</Check>
          <div className="set-help">Only what today’s brief offers to tidy. The brief says what went, and one click takes it all back.</div>
          <Check checked={p.draft_ahead} onChange={(draft_ahead) => setPrefs({ draft_ahead })}>Draft replies ahead for important mail</Check>
          <div className="set-help" id="aiDraftAhead">Written while you’re using MailMind, within the daily limit, so a reply is ready when you open the message. Off, a reply is drafted only when you ask for one.{ahead ? ` ${ahead}` : ""}</div>
        </div>
      )}
    </div>
  );
}
