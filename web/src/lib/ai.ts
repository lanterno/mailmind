// What the page says of AI (#49). Which provider is chosen and why AI is off are the digest's to say (`ai`, as the server has
// them in force); the page keeps only its words for each.
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

// Why AI is off, in plain words for the provider chosen: "" while it is on.
export function whyOff(ai: Said | null | undefined): string {
  if (!ai?.off) return "";
  const name = PROVIDER_NAME[ai.provider];
  if (ai.off === "turned-off") return "it is turned off in Settings";
  if (ai.off === "no-key") return `there’s no key for ${name}`;
  if (ai.off === "no-url") return `${name} has no URL`;
  return `no model is chosen for ${name}`;
}

// How AI stands, in a line: on with which provider and model, and the last call's failure; or off, and why.
export function aiStanding(ai: AiStatus | null | undefined): string {
  if (!ai) return "";
  if (ai.off === "turned-off") return "AI is turned off.";
  if (ai.off) return `AI is off: ${whyOff(ai)}.`;
  const failed = ai.last_ok === false && ai.last_error ? ` The last call failed: ${ai.last_error}.` : "";
  return `AI is on: ${PROVIDER_LABEL[ai.provider]} · ${ai.model}.${failed}`;
}
