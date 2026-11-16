// What the page says of AI, and whether it offers what only AI can do (#49). Which provider is chosen and why AI is off are the
// digest's to say (`ai`, as the server has them in force); the page keeps only its words for each.
import type { AiProvider, AiStatus } from "./api/types";

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

// Who the text of a message goes to, for each provider that isn't on this Mac: the company, or the service named.
const RECIPIENT: Record<Exclude<AiProvider, "local">, string> = {
  anthropic: "Anthropic",
  openai: "OpenAI",
  gemini: "Google",
  compatible: "the OpenAI-compatible service",
};

// What AI sends off this Mac, and to whom: the provider chosen, or nothing at all when it answers here. `baseUrl` is the
// preference as set, "" for the provider's own.
export function aiPrivacy(provider: AiProvider, baseUrl: string): string {
  if (provider === "local") return "With a model on this Mac, the text of a message never leaves this Mac.";
  if (runsHere(provider, baseUrl)) return `With ${PROVIDER_NAME[provider]} on this Mac, the text of a message never leaves this Mac.`;
  return `With AI on, the text of a message goes to ${RECIPIENT[provider]} only when it is summarized, briefed, drafted or asked about; nothing else leaves this machine.`;
}

// What AI mode sends, to whom and when, said before it is turned on as after: under its switch and in Help. A model on this
// Mac, or a service at a loopback address, sends nothing off this Mac. `baseUrl` is the preference as set.
export function aiSends(provider: AiProvider, baseUrl: string): string {
  if (provider === "local") return "With a model on this Mac, nothing leaves this Mac.";
  if (runsHere(provider, baseUrl)) return `With ${PROVIDER_NAME[provider]} on this Mac, nothing leaves this Mac.`;
  return `With AI mode on, each new message’s sender, subject and opening text go to ${RECIPIENT[provider]} to be summarized, automatically. `
    + "Whole conversations go when you ask for a summary, a draft or the brief, with what you type into Ask, a search written as a "
    + "sentence, or Rewrite. Attachments never leave this Mac.";
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
