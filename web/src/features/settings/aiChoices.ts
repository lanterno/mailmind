// The AI tab's rules, with no DOM (#49): what it shows with AI mode off and on, what each provider asks of a person, the
// models found on this Mac and what picking one changes, what choosing another provider changes, what the
// model menu offers and in what order, what a URL typed saves as, and how a key typed leaves the page.
import type { AiProvider, GetAnswers, PostAnswers, PostBodies } from "../../lib/api/types";
import type { Store } from "../../lib/store";

type Changes = PostBodies["/api/preferences"];
type Kept = PostAnswers["/api/ai/key"];
export type LocalServer = GetAnswers["/api/ai/local"]["servers"][number];

// What the AI tab shows: AI mode's switch, and only once it is on the connection (the provider, then as it needs a key, a
// model and a URL, and how AI stands), then spending and what is written ahead.
export type AiPart = "mode" | "connection" | "spending" | "ahead";
export const aiParts = (on: boolean): AiPart[] => (on ? ["mode", "connection", "spending", "ahead"] : ["mode"]);

// What the AI tab's header says: where its choices and keys are kept. What AI mode sends, and to whom, is under its switch.
export const AI_KEPT = "Kept on this Mac, with each key in your login Keychain.";

// What each provider asks of a person: a key (needed, optional or none) and where one is made; the URL it is reached at, which
// a person gives for a model on this Mac (Ollama's, filled in) and for a compatible service (none until given), and Claude's
// and the rest never; and whether it has a model of its own, or one must be chosen.
interface Needs { key: "needed" | "optional" | "none"; keysAt?: string; url: string | null; ownModel: boolean }
export const NEEDS: Record<AiProvider, Needs> = {
  anthropic: { key: "needed", keysAt: "https://console.anthropic.com", url: null, ownModel: true },
  openai: { key: "needed", keysAt: "https://platform.openai.com/api-keys", url: null, ownModel: true },
  gemini: { key: "needed", keysAt: "https://aistudio.google.com/apikey", url: null, ownModel: true },
  local: { key: "optional", url: "http://127.0.0.1:11434/v1", ownModel: false },
  compatible: { key: "optional", url: "", ownModel: false },
};

// How a provider whose key is paid for bills it, in plain words on its key's card: per use, by the provider, apart from any
// chat subscription, and where credit is added (for Gemini, the project a key belongs to is billed). A model on this Mac costs
// nothing, and a compatible service says its own.
const APART = "separately from any ChatGPT or Claude subscription.";
export interface Billing { words: string; creditAt?: string }
export const BILLING: Partial<Record<AiProvider, Billing>> = {
  anthropic: { words: `Billed per use by Anthropic, ${APART} Add credit in Anthropic’s console, under Billing.`, creditAt: "https://console.anthropic.com/settings/billing" },
  openai: { words: `Billed per use by OpenAI, ${APART} Add credit on OpenAI’s platform, under Billing.`, creditAt: "https://platform.openai.com/settings/organization/billing" },
  gemini: { words: `Billed per use by Google, ${APART} Use a key on a billed project: on the free tier, a key stops each day at its quota.` },
};

// Another provider: its own model and URL until a person chooses others, as the server sets them back.
export const providerChanges = (provider: AiProvider): Changes => ({ ai_provider: provider, ai_model: "", ai_base_url: "" });

export interface ModelChoice { value: string; label: string }

// The model menu: the provider's own first, as "Default (its name)" ("Default" before the provider has said), then the models
// it lists, and the one chosen where the list doesn't name it. With no model of its own, a menu with none chosen asks for one.
export function modelChoices(listed: readonly string[], own: string | null | undefined, chosen: string): ModelChoice[] {
  const first = own === null
    ? chosen ? [] : [{ value: "", label: "Choose a model" }]
    : [{ value: "", label: own ? `Default (${own})` : "Default" }];
  const names = listed.filter((name) => name !== own || name === chosen);
  if (chosen && !names.includes(chosen)) names.unshift(chosen);
  return [...first, ...names.map((name) => ({ value: name, label: name }))];
}

// What a URL typed saves as: "" for the provider's own, filled in and left as it was, and no slash at its end, as the server
// keeps it.
export function urlToSave(typed: string, prefill: string): string {
  const url = typed.trim().replace(/\/+$/, "");
  return url === prefill.replace(/\/+$/, "") ? "" : url;
}

// A model on this Mac, picked from what a server found here offers: its URL and the model, saved as one change. The URL is ""
// where it is a model on this Mac's own (Ollama's), as a URL typed saves.
const pick = (url: string, model: string) => JSON.stringify([url, model]);
export function localPicked(value: string): Changes {
  const [url, model] = JSON.parse(value) as [string, string];
  return { ai_base_url: urlToSave(url, NEEDS.local.url!), ai_model: model };
}

export interface LocalMenu {
  value: string;                                                    // what the menu shows chosen
  first: ModelChoice | null;                                        // "Choose a model", or the one chosen where no server found offers it
  groups: { name: string; url: string; models: ModelChoice[] }[];  // each server found, by name, with the models it offers
}
export const CHOSEN_ELSEWHERE = "chosen";

// The menu of models on this Mac: each server found, in the finder's order, with its models; the model chosen shown chosen
// where the server at the URL saved offers it, else on its own line first; and "Choose a model" while none is.
export function localMenu(servers: readonly LocalServer[], savedUrl: string, chosen: string): LocalMenu {
  const at = chosen ? servers.find((s) => urlToSave(s.url, NEEDS.local.url!) === savedUrl && s.models.includes(chosen)) : undefined;
  const groups = servers.map((s) => ({ name: s.name, url: s.url, models: s.models.map((m) => ({ value: pick(s.url, m), label: m })) }));
  if (at) return { value: pick(at.url, chosen), first: null, groups };
  if (chosen) return { value: CHOSEN_ELSEWHERE, first: { value: CHOSEN_ELSEWHERE, label: chosen }, groups };
  return { value: "", first: { value: "", label: "Choose a model" }, groups };
}

// What the search for model servers on this Mac says it found.
export function localFound(servers: readonly LocalServer[]): string {
  if (!servers.length) return "No model server is running on this Mac. Start one, Ollama or LM Studio for example, and look again.";
  const empty = servers.filter((s) => !s.models.length).map((s) => s.name);
  const names = servers.map((s) => s.name);
  const found = `Running on this Mac: ${names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names.at(-1)}` : names[0]}.`;
  return empty.length ? `${found} ${empty.join(" and ")} ${empty.length > 1 ? "have" : "has"} no model to chat with yet.` : found;
}

// The key typed, sent to be kept: it leaves the field as it goes, before any answer, so nothing on the page holds it after the
// POST whatever the server says. Nothing is sent for an empty field.
export async function keepKey(field: Store<string>, provider: AiProvider, send: (body: PostBodies["/api/ai/key"]) => Promise<Kept>): Promise<Kept | null> {
  const key = field.get().trim();
  field.set("");
  if (!key) return null;
  return await send({ provider, key });
}
