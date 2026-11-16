// What the page says of AI, and whether it offers what only AI can do (#49). Which provider is chosen and why AI is off are the
// digest's to say (`ai`, as the server has them in force); the page keeps only its words for each.
import type { AiProvider, AiStatus } from "./api/types";

type Said = Pick<AiStatus, "off" | "provider">;

// Each provider as its menu names it, in the menu's order.
export const PROVIDER_LABEL: Record<AiProvider, string> = {
  anthropic: "Claude (Anthropic)",
  openai: "OpenAI",
  gemini: "Google Gemini",
  ollama: "Ollama, on this Mac",
  compatible: "Another OpenAI-compatible service",
};

// Each provider as a sentence names it.
export const PROVIDER_NAME: Record<AiProvider, string> = {
  anthropic: "Claude",
  openai: "OpenAI",
  gemini: "Gemini",
  ollama: "Ollama",
  compatible: "the OpenAI-compatible service",
};

// Whether what only AI can do is offered. Turned off in Settings, none of it shows and nothing says it is missing. Off for want
// of a key, a model or a URL, it still shows, and says how to set AI up. Before the digest has come, nothing is hidden.
export const aiOffered = (ai: Said | null | undefined) => ai?.off !== "turned-off";

// Whether the provider answers on this Mac: Ollama at its own address, or any service at a loopback one.
const HERE = /^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?(\/|$)/i;
export const runsHere = (provider: AiProvider, baseUrl: string) => (provider === "ollama" && !baseUrl) || HERE.test(baseUrl);

// Who the text of a message goes to, for each provider: the company, or the server named.
const RECIPIENT: Record<AiProvider, string> = {
  anthropic: "Anthropic",
  openai: "OpenAI",
  gemini: "Google",
  ollama: "your Ollama server",
  compatible: "the OpenAI-compatible service",
};

// What AI sends off this Mac, and to whom: the provider chosen, or nothing at all when it answers here. `baseUrl` is the
// preference as set, "" for the provider's own.
export function aiPrivacy(provider: AiProvider, baseUrl: string): string {
  const name = PROVIDER_NAME[provider];
  if (runsHere(provider, baseUrl)) return `${name[0]!.toUpperCase()}${name.slice(1)} runs on this Mac, so the text of your mail never leaves it.`;
  return `With AI on, the text of a message goes to ${RECIPIENT[provider]} only when it is summarized, briefed, drafted or asked about; nothing else leaves this machine.`;
}

// Why AI is off, in plain words for the provider chosen: "" while it is on.
export function whyOff(ai: Said | null | undefined): string {
  if (!ai?.off) return "";
  const name = PROVIDER_NAME[ai.provider];
  if (ai.off === "turned-off") return "it is turned off in Settings";
  if (ai.off === "no-key") return `there’s no key for ${name}`;
  if (ai.off === "no-url") return `${name} has no URL`;
  return `no model is chosen for ${name}`;
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
