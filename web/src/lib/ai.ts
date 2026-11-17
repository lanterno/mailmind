// What the page says of AI, and whether it offers what only AI can do (#49). Which provider is chosen and why AI is off are the
// digest's to say (`ai`, as the server has them in force); the page keeps only its words for each.
import type { AiProvider, AiStatus } from "./api/types";
import type { Prefs } from "./prefs";

type Said = Pick<AiStatus, "off" | "provider">;

// Each provider as its menu names it, in the menu's order.
export const PROVIDER_LABEL: Record<AiProvider, string> = {
  anthropic: "Claude (Anthropic)",
  openai: "OpenAI",
  gemini: "Google Gemini",
  local: "A model on this Mac",
  compatible: "Another OpenAI-compatible service",
};

// Each provider as a sentence names it. A model on this Mac is no one's: the server running it is named only as one found.
export const PROVIDER_NAME: Record<AiProvider, string> = {
  anthropic: "Claude",
  openai: "OpenAI",
  gemini: "Gemini",
  local: "a model on this Mac",
  compatible: "the OpenAI-compatible service",
};

// Whether what only AI can do is offered. Turned off in Settings, none of it shows and nothing says it is missing. Off for want
// of a key, a model or a URL, it still shows, and says how to set AI up. Before the digest has come, nothing is hidden.
export const aiOffered = (ai: Said | null | undefined) => ai?.off !== "turned-off";

// Whether the provider answers on this Mac: a model on this Mac, which the server holds there, or any service at a loopback
// address.
const HERE = /^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?(\/|$)/i;
export const runsHere = (provider: AiProvider, baseUrl: string) => provider === "local" || HERE.test(baseUrl);

// Who the text of a message goes to, for each provider that isn't on this Mac: the company, or the service at the URL given.
// The Mac app says the same (`AiProvider.recipient`).
const RECIPIENT: Record<Exclude<AiProvider, "local">, string> = {
  anthropic: "Anthropic",
  openai: "OpenAI",
  gemini: "Google",
  compatible: "the service at the URL you gave",
};

// What decides what AI mode sends: the provider and its URL as set, the model chosen, whether that model runs in a cloud though
// it is served on this Mac (the server says so, as `cloud_model`: Ollama's `-cloud` models), and whether replies are drafted ahead.
export interface Sends {
  provider: AiProvider;
  baseUrl: string;
  model: string;
  cloud: boolean;
  draftAhead: boolean;
}

// The same, from the digest's `ai` and the preferences, for the provider in force or `provider`, one chosen since. The server
// says whether a model runs in a cloud only for the provider it holds, and names the model only while AI is on.
export function sendsOf(ai: Pick<AiStatus, "provider" | "model" | "cloud_model"> | null | undefined,
  p: Pick<Prefs, "ai_provider" | "ai_base_url" | "ai_model" | "draft_ahead">, provider: AiProvider = ai?.provider ?? p.ai_provider): Sends {
  const said = ai?.provider === provider ? ai : null;
  return { provider, baseUrl: p.ai_base_url, model: said?.model || p.ai_model, cloud: said?.cloud_model === true, draftAhead: p.draft_ahead };
}

// Where mail goes for a model served on this Mac: nowhere, unless the model runs in Ollama's cloud. `ahead` is what drafting
// ahead adds, said only then. The Mac app's `AIWords.sends` says the same.
function servedHere(s: Sends, ahead: string): string {
  if (!s.cloud) return s.provider === "local" ? "With a model on this Mac, nothing leaves this Mac." : "With the service on this Mac, nothing leaves this Mac.";
  return `${s.model || "The model chosen"} runs in Ollama’s cloud, so what is summarized goes to Ollama’s servers.${s.draftAhead ? ` ${ahead}` : ""}`;
}

// Who mail goes to off this Mac: the company behind the provider, or the service named; null for one served here.
const awayTo = ({ provider, baseUrl }: Sends) => (provider === "local" || runsHere(provider, baseUrl) ? null : RECIPIENT[provider]);

const DRAFTED_AHEAD = "With Draft replies ahead on, whole conversations of important mail go too, to draft replies before you open them.";

// What AI mode sends, to whom and when, said before it is turned on as after: under its switch, where AI is off, and in Help.
// A model on this Mac, or a service at a loopback address, sends nothing off this Mac unless the model runs in a cloud.
export function aiSends(s: Sends): string {
  const to = awayTo(s);
  if (!to) return servedHere(s, DRAFTED_AHEAD);
  return `With AI mode on, the sender, subject and opening text of each message in your Inbox go to ${to} to be summarized, `
    + "automatically: the last 30 days’ as soon as you turn it on, then each new one. Those senders, subjects and summaries go "
    + "again to write the day’s brief and what came in since you looked. Whole conversations go only when you ask: for a summary "
    + "or a draft, with what you type into Ask, a search written as a sentence, or Rewrite. Attachments never leave this Mac."
    + `${s.draftAhead ? ` ${DRAFTED_AHEAD}` : ""}`;
}

// The same in short, for the sync menu with AI on: what goes on its own, that more goes when asked, and never an attachment.
export function aiSendsInShort(s: Sends): string {
  const to = awayTo(s), ahead = "With Draft replies ahead on, whole conversations of important mail go too.";
  if (!to) return servedHere(s, ahead);
  return `The sender, subject and opening text of each message in your Inbox go to ${to} automatically, and more when you ask `
    + `for something.${s.draftAhead ? ` ${ahead}` : ""} Attachments never leave this Mac.`;
}

// Who what is written ahead goes to: the provider's company or service, Ollama's servers for a model here that runs in its
// cloud, or null when it is written on this Mac.
const aheadTo = (s: Sends) => awayTo(s) ?? (s.cloud ? "Ollama’s servers" : null);

// What drafting ahead sends, said under its switch, as the Mac app's `AIWords.aheadGoes` says it.
export function draftAheadSends(s: Sends): string {
  const to = aheadTo(s);
  return to ? `On, whole conversations of important mail go to ${to} to draft them.` : "On, they are drafted on this Mac, and nothing leaves it.";
}

// Why AI is off, in plain words for the provider chosen: "" while it is on.
export function whyOff(ai: Said | null | undefined): string {
  if (!ai?.off) return "";
  const name = PROVIDER_NAME[ai.provider];
  if (ai.off === "turned-off") return "it is turned off in Settings";
  if (ai.off === "no-key") return `there’s no key for ${name}`;
  if (ai.off === "no-url") return `${name} has no URL`;
  return ai.provider === "local" ? "no model on this Mac is chosen" : `no model is chosen for ${name}`;
}

// AI off, and why, for a place that needs it: "AI is off" alone where the digest doesn't say (or says it is on).
export function offLine(ai: Said | null | undefined): string {
  const why = whyOff(ai);
  return why ? `AI is off: ${why}` : "AI is off";
}

// How AI stands, in a line: on with which provider and model, and the last call's failure; or off, and why.
export function aiStanding(ai: AiStatus | null | undefined): string {
  if (!ai) return "";
  if (ai.off === "turned-off") return "AI is turned off.";
  if (ai.off) return `${offLine(ai)}.`;
  const failed = ai.last_ok === false && ai.last_error ? ` The last call failed: ${ai.last_error}.` : "";
  return `AI is on: ${PROVIDER_LABEL[ai.provider]} · ${ai.model}.${failed}`;
}
