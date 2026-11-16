import { describe, expect, it } from "vitest";
import { aiStanding, PROVIDER_LABEL, whyOff } from "./ai";
import type { AiStatus } from "./api/types";

const status = (over: Partial<AiStatus> = {}): AiStatus => ({
  engine: "api", provider: "anthropic", off: null, model: "claude-opus-5-5", key_from: "mailmind.config.json",
  last_ok: null, last_ts: 0, last_error: null, limit_micros: 15_000_000, spent_today_micros: 0, ...over,
});
const off = (why: AiStatus["off"], provider: AiStatus["provider"] = "anthropic") => status({ engine: "none", model: "", key_from: "", off: why, provider });

describe("the providers", () => {
  it("are named in the menu's words, in its order", () => {
    expect(Object.values(PROVIDER_LABEL)).toEqual(["Claude (Anthropic)", "OpenAI", "Google Gemini", "Ollama, on this Mac", "Another OpenAI-compatible service"]);
  });
});

describe("why AI is off", () => {
  it("is nothing while it is on", () => expect(whyOff(status())).toBe(""));
  it("names the provider chosen", () => {
    expect(whyOff(off("no-key", "openai"))).toBe("there’s no key for OpenAI");
    expect(whyOff(off("no-key"))).toBe("there’s no key for Claude");
    expect(whyOff(off("no-model", "ollama"))).toBe("no model is chosen for Ollama");
    expect(whyOff(off("no-url", "compatible"))).toBe("the OpenAI-compatible service has no URL");
    expect(whyOff(off("turned-off", "gemini"))).toBe("it is turned off in Settings");
  });
});

describe("how AI stands", () => {
  it("says the provider and model on, and the last call's failure", () => {
    expect(aiStanding(status())).toBe("AI is on: Claude (Anthropic) · claude-opus-5-5.");
    expect(aiStanding(status({ provider: "openai", model: "gpt-6.1-sol", last_ok: false, last_error: "the API key was rejected" })))
      .toBe("AI is on: OpenAI · gpt-6.1-sol. The last call failed: the API key was rejected.");
  });
  it("says why it is off", () => {
    expect(aiStanding(off("turned-off"))).toBe("AI is turned off.");
    expect(aiStanding(off("no-key", "gemini"))).toBe("AI is off: there’s no key for Gemini.");
  });
  it("says nothing before the digest has come", () => expect(aiStanding(undefined)).toBe(""));
});
