// AI (#49): whether MailMind uses it at all, which provider, with what key, model and URL, and what it may spend. Each choice
// saves on its own, as Preferences' do, and the digest is read again so the page shows what is now in force. A key goes to the
// server once, which keeps it in the login Keychain, and never comes back: the page says only whether one is kept. The provider
// is asked for its models only when a person acts here (chooses it, keeps a key, opens the menu, asks again) or while this tab
// is open with AI on, never on its own.
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Input, Label, NumberField } from "react-aria-components";
import { aiStanding, PROVIDER_LABEL, PROVIDER_NAME } from "../../lib/ai";
import { get, post, refusalOf } from "../../lib/api/client";
import { LIMITS } from "../../lib/api/limits.gen";
import type { AiProvider } from "../../lib/api/types";
import { entries } from "../../lib/entries";
import { prefs, setPrefs } from "../../lib/prefs";
import { createStore } from "../../lib/store";
import { toast } from "../../lib/toast";
import { Icon } from "../../lib/ui/Icon";
import { useDigest } from "../mailbox";
import { keepKey, modelChoices, NEEDS, providerChanges, urlToSave } from "./aiChoices";
import { Check } from "./Check";
import { useStepBack } from "./useStepBack";

// The key for the provider chosen: kept or not, a field to keep one, and Remove for one kept.
function KeyField({ provider, kept, keptFrom, done }: { provider: AiProvider; kept: boolean; keptFrom: string; done: (kept: AiProvider[]) => void }) {
  const [field] = useState(() => createStore(""));
  const typed = field.useValue();
  const [busy, setBusy] = useState(false);
  const [refused, setRefused] = useState("");
  const needs = NEEDS[provider];
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
        {needs.key === "optional" ? "Only if the service asks for one. " : ""}Kept in your login Keychain, never in a file.
        {!kept && keptFrom ? ` MailMind is using the key in ${keptFrom} until you keep one here.` : ""}
      </div>
      {needs.keysAt && (
        <a className="set-mini set-link" href={needs.keysAt} target="_blank" rel="noopener noreferrer">
          Make a key at {needs.keysAt.replace(/^https:\/\//, "")} <Icon name="external" />
        </a>
      )}
      <div className="err" role="alert">{refused}</div>
    </>
  );
}

// A model's name as the provider writes it, for one its list doesn't offer.
function ModelByHand({ chosen, use, back }: { chosen: string; use: (model: string) => void; back: () => void }) {
  const [typed, setTyped] = useState(chosen);
  return (
    <div className="ai-row">
      <input
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

// The service's URL, filled in with Ollama's own: saved as a person leaves it, "" where it is the provider's own.
function UrlField({ saved, prefill, save }: { saved: string; prefill: string; save: (url: string) => void }) {
  const [typed, setTyped] = useState(saved || prefill);
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

export function AiForm() {
  const p = prefs.useValue();
  const qc = useQueryClient();
  const { data: digest } = useDigest();
  useStepBack();
  const provider = p.ai_provider, needs = NEEDS[provider], name = PROVIDER_NAME[provider];
  const [acted, setActed] = useState(false);     // something done here may ask the provider, with AI on or off
  const [byHand, setByHand] = useState(false);
  // The provider and URL the server holds, which its models are asked by: a choice counts once it is saved.
  const [held, setHeld] = useState({ provider, url: p.ai_base_url });
  const keys = useQuery({ queryKey: ["aiKeys"], queryFn: () => get("/api/ai/key") });
  const kept = keys.data?.kept.includes(provider) ?? false;
  const ai = digest?.ai;
  const keptFrom = ai?.provider === provider && ai.key_from && ai.key_from !== "mailmind.config.json" ? ai.key_from : "";
  const wants = needs.key === "needed" && !kept && !keptFrom ? "key" : needs.url === "" && !p.ai_base_url ? "url" : null;
  const current = held.provider === provider;
  const models = useQuery({
    queryKey: ["aiModels", held.provider, held.url, kept],
    queryFn: () => get(`/api/ai/models?provider=${held.provider}`),
    enabled: current && keys.data !== undefined && wants === null && (p.ai_enabled || acted),
    staleTime: Infinity,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  const inForce = () => void qc.invalidateQueries({ queryKey: ["digest"] });
  const choose = (next: AiProvider) => {
    if (next === provider) return;
    setActed(true);
    setByHand(false);
    setPrefs(providerChanges(next), () => {
      setHeld({ provider: next, url: "" });
      inForce();
    });
  };
  const chooseUrl = (ai_base_url: string) => {
    setPrefs({ ai_base_url }, () => {
      setActed(true);
      setHeld({ provider, url: ai_base_url });
      inForce();
    });
  };
  const keysKept = (kept: AiProvider[]) => {
    setActed(true);
    qc.setQueryData(["aiKeys"], { ok: true, kept });
    inForce();
  };
  const chooseModel = (ai_model: string) => {
    setByHand(false);
    if (ai_model !== p.ai_model) setPrefs({ ai_model }, inForce);
  };
  const askAgain = () => {
    setActed(true);
    void models.refetch();
  };
  const listedNow = current ? models.data : undefined;
  const own = listedNow ? listedNow.default : needs.ownModel ? undefined : null;
  const choices = modelChoices(listedNow?.models ?? [], own, p.ai_model);
  let listed = "";
  if (current && models.isFetching) listed = `Asking ${name} for its models…`;
  else if (current && models.isError) listed = refusalOf(models.error)?.detail || "Couldn’t reach the provider.";
  else if (!listedNow && wants === "key") listed = `Keep a key for ${name}, and its models are listed here.`;
  else if (!listedNow && wants === "url") listed = "Give the service’s URL, and its models are listed here.";
  else if (!needs.ownModel && !p.ai_model) listed = `${name.charAt(0).toUpperCase()}${name.slice(1)} has no model of its own: choose one.`;
  return (
    <>
      <div className="set-body" id="aiBody">
        <div className="set-form">
          <Check checked={p.ai_enabled} onChange={(ai_enabled) => setPrefs({ ai_enabled }, inForce)}>Use AI</Check>
          <div className="set-help">Summaries, the brief, drafts and Ask. Off, MailMind sends nothing to any AI service.</div>
        </div>
        <div className="set-form">
          <label className="lbl" htmlFor="aiProvider">Provider</label>
          <select id="aiProvider" value={provider} onChange={(e) => choose(e.target.value as AiProvider)}>
            {entries(PROVIDER_LABEL).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
          </select>
          {needs.key !== "none" && <KeyField key={provider} provider={provider} kept={kept} keptFrom={keptFrom} done={keysKept} />}
          <label className="lbl" htmlFor="aiModel">Model</label>
          {byHand
            ? <ModelByHand chosen={p.ai_model} use={chooseModel} back={() => setByHand(false)} />
            : (
              <div className="ai-row">
                <select id="aiModel" value={p.ai_model} onFocus={() => setActed(true)} onChange={(e) => chooseModel(e.target.value)}>
                  {choices.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                </select>
                <button type="button" className="set-mini" aria-label="Ask the provider for its models again" title="Ask the provider for its models again" disabled={current && models.isFetching} onClick={askAgain}><Icon name="refresh" /></button>
                <button type="button" className="set-mini" onClick={() => setByHand(true)}>Type a name</button>
              </div>
            )}
          <div className="set-help" role="status">{listed}</div>
          {needs.url !== null && (
            <>
              <label className="lbl" htmlFor="aiUrl">The service’s URL</label>
              <UrlField key={`${provider}:${p.ai_base_url}`} saved={p.ai_base_url} prefill={needs.url} save={chooseUrl} />
              <div className="set-help">{provider === "ollama" ? "Where Ollama answers on this Mac." : "Where the service answers, up to /v1: https, or http on this Mac."}</div>
            </>
          )}
        </div>
        {p.ai_enabled && (
          <div className="set-form">
            <div className="lbl">Spending and work ahead</div>
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
            <Check checked={p.morning_brief} onChange={(morning_brief) => setPrefs({ morning_brief })}>Have today’s brief ready each morning</Check>
            <div className="set-help">Written before you open your Inbox, within the daily limit, and only if you’ve used MailMind in the last two days.</div>
            <Check checked={p.weekly_review} onChange={(weekly_review) => setPrefs({ weekly_review })}>Have last week’s review ready each Monday</Check>
            <div className="set-help">Written on Monday morning, within the daily limit, and only if you’ve used MailMind in the last two days. Your week shows it until the next Monday, and ↻ there writes a new one.</div>
            <Check checked={p.auto_tidy} onChange={(auto_tidy) => setPrefs({ auto_tidy })}>File low-priority mail by itself, once a day</Check>
            <div className="set-help">Only what today’s brief offers to tidy. The brief says what went, and one click takes it all back.</div>
          </div>
        )}
      </div>
      <div className="set-foot">
        <span className="set-hint" id="aiStanding" role="status">{aiStanding(ai)}</span>
      </div>
    </>
  );
}
