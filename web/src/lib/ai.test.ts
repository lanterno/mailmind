import { describe, expect, it } from "vitest";
import { aiOffered, aiPrivacy, aiStanding, offLine, PROVIDER_LABEL, runsHere, whyOff } from "./ai";
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

describe("what only AI can do", () => {
  it("is offered while AI is on, and off for want of a key, a model or a URL", () => {
    expect(aiOffered(status())).toBe(true);
    for (const why of ["no-key", "no-model", "no-url"] as const) expect(aiOffered(off(why))).toBe(true);
  });
  it("isn't once AI is turned off", () => expect(aiOffered(off("turned-off", "ollama"))).toBe(false));
  it("is, before the digest has come", () => expect(aiOffered(undefined)).toBe(true));
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

describe("AI off, where something needs it", () => {
  it("says why, for the provider chosen", () => {
    expect(offLine(off("no-key", "openai"))).toBe("AI is off: there’s no key for OpenAI");
    expect(offLine(off("no-url", "compatible"))).toBe("AI is off: the OpenAI-compatible service has no URL");
  });
  it("says only that it is off where the digest doesn't say why", () => {
    expect(offLine(undefined)).toBe("AI is off");
    expect(offLine(status())).toBe("AI is off");     // the digest a moment behind the server
  });
});

describe("what leaves this Mac", () => {
  it("goes to the company behind the provider chosen, and to no one else", () => {
    expect(aiPrivacy("anthropic", "")).toBe("With AI on, the text of a message goes to Anthropic only when it is summarized, briefed, drafted or asked about; nothing else leaves this machine.");
    expect(aiPrivacy("openai", "")).toContain("goes to OpenAI only");
    expect(aiPrivacy("gemini", "")).toContain("goes to Google only");
    expect(aiPrivacy("compatible", "https://llm.example.test/v1")).toContain("goes to the OpenAI-compatible service only");
    expect(aiPrivacy("ollama", "http://gpu.example.test:11434/v1")).toContain("goes to your Ollama server only");
  });
  it("is nothing when the provider answers on this Mac", () => {
    expect(aiPrivacy("ollama", "")).toBe("Ollama runs on this Mac, so the text of your mail never leaves it.");
    expect(aiPrivacy("compatible", "http://localhost:8080/v1")).toBe("The OpenAI-compatible service runs on this Mac, so the text of your mail never leaves it.");
  });
  it("is on this Mac only at a loopback address, or Ollama at its own", () => {
    for (const url of ["http://127.0.0.1:11434/v1", "http://localhost/v1", "https://[::1]:8443/v1", "HTTP://LOCALHOST:1234"]) expect(runsHere("compatible", url)).toBe(true);
    for (const url of ["", "https://127.0.0.1.example.test/v1", "http://localhost.example.test/v1", "http://10.0.0.2:11434/v1"]) expect(runsHere("compatible", url)).toBe(false);
    expect(runsHere("ollama", "")).toBe(true);
    expect(runsHere("anthropic", "")).toBe(false);
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
