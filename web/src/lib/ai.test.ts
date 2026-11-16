import { describe, expect, it } from "vitest";
import { aiOffered, aiSends, aiSendsInShort, aiStanding, offLine, PROVIDER_LABEL, PROVIDER_NAME, runsHere, whyOff } from "./ai";
import type { AiStatus } from "./api/types";

const status = (over: Partial<AiStatus> = {}): AiStatus => ({
  engine: "api", provider: "anthropic", off: null, model: "claude-opus-5-5", background_model: "claude-haiku-5-5", key_from: "mailmind.config.json",
  last_ok: null, last_ts: 0, last_error: null, limit_micros: 15_000_000, spent_today_micros: 0, ...over,
});
const off = (why: AiStatus["off"], provider: AiStatus["provider"] = "anthropic") => status({ engine: "none", model: "", key_from: "", off: why, provider });

describe("the providers", () => {
  it("are named in the menu's words, in its order", () => {
    expect(Object.values(PROVIDER_LABEL)).toEqual(["Claude (Anthropic)", "OpenAI", "Google Gemini", "A model on this Mac", "Another OpenAI-compatible service"]);
  });
});

describe("a model on this Mac", () => {
  it("is named as no one's, and Ollama nowhere", () => {
    expect(PROVIDER_LABEL.local).toBe("A model on this Mac");
    expect(PROVIDER_NAME.local).toBe("a model on this Mac");
    expect(JSON.stringify([PROVIDER_LABEL, PROVIDER_NAME])).not.toMatch(/ollama/i);
  });
});

describe("what only AI can do", () => {
  it("is offered while AI is on, and off for want of a key, a model or a URL", () => {
    expect(aiOffered(status())).toBe(true);
    for (const why of ["no-key", "no-model", "no-url"] as const) expect(aiOffered(off(why))).toBe(true);
  });
  it("isn't once AI is turned off", () => expect(aiOffered(off("turned-off", "local"))).toBe(false));
  it("is, before the digest has come", () => expect(aiOffered(undefined)).toBe(true));
});

describe("why AI is off", () => {
  it("is nothing while it is on", () => expect(whyOff(status())).toBe(""));
  it("names the provider chosen", () => {
    expect(whyOff(off("no-key", "openai"))).toBe("there’s no key for OpenAI");
    expect(whyOff(off("no-key"))).toBe("there’s no key for Claude");
    expect(whyOff(off("no-model", "local"))).toBe("no model on this Mac is chosen");
    expect(whyOff(off("no-model", "compatible"))).toBe("no model is chosen for the OpenAI-compatible service");
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
  it("says in short, for the sync menu, that new mail goes on its own and attachments never", () => {
    expect(aiSendsInShort("anthropic", "")).toBe("Each new message’s sender, subject and opening text go to Anthropic automatically, "
      + "and more when you ask for something. Attachments never leave this Mac.");
    expect(aiSendsInShort("openai", "")).toContain(" go to OpenAI automatically");
    expect(aiSendsInShort("gemini", "")).toContain(" go to Google automatically");
    expect(aiSendsInShort("compatible", "https://llm.example.test/v1")).toContain(" go to the OpenAI-compatible service automatically");
  });
  it("says in short that nothing leaves this Mac when the provider answers here", () => {
    expect(aiSendsInShort("local", "")).toBe("With a model on this Mac, nothing leaves this Mac.");
    expect(aiSendsInShort("local", "http://localhost:1234/v1")).toBe("With a model on this Mac, nothing leaves this Mac.");
    expect(aiSendsInShort("compatible", "http://localhost:8080/v1")).toBe("With the OpenAI-compatible service on this Mac, nothing leaves this Mac.");
  });
  it("says before AI mode is on what goes to the company behind the provider, when, and that attachments never do", () => {
    expect(aiSends("anthropic", "")).toBe("With AI mode on, each new message’s sender, subject and opening text go to Anthropic to be "
      + "summarized, automatically. Whole conversations go when you ask for a summary, a draft or the brief, with what you type "
      + "into Ask, a search written as a sentence, or Rewrite. Attachments never leave this Mac.");
    expect(aiSends("openai", "")).toContain(" go to OpenAI to be summarized");
    expect(aiSends("gemini", "")).toContain(" go to Google to be summarized");
    expect(aiSends("compatible", "https://llm.example.test/v1")).toContain(" go to the OpenAI-compatible service to be summarized");
  });
  it("says nothing leaves this Mac when the provider answers here", () => {
    expect(aiSends("local", "")).toBe("With a model on this Mac, nothing leaves this Mac.");
    expect(aiSends("compatible", "http://localhost:8080/v1")).toBe("With the OpenAI-compatible service on this Mac, nothing leaves this Mac.");
  });
  it("is on this Mac only at a loopback address, or for a model on this Mac, which the server holds there", () => {
    for (const url of ["http://127.0.0.1:11434/v1", "http://localhost/v1", "https://[::1]:8443/v1", "HTTP://LOCALHOST:1234"]) expect(runsHere("compatible", url)).toBe(true);
    for (const url of ["", "https://127.0.0.1.example.test/v1", "http://localhost.example.test/v1", "http://10.0.0.2:11434/v1"]) expect(runsHere("compatible", url)).toBe(false);
    expect(runsHere("local", "")).toBe(true);
    expect(runsHere("anthropic", "")).toBe(false);
  });
});

describe("how AI stands", () => {
  it("says the provider and model on, and the last call's failure", () => {
    expect(aiStanding(status())).toBe("AI is on: Claude (Anthropic) · claude-opus-5-5.");
    expect(aiStanding(status({ provider: "local", model: "qwen3:8b" }))).toBe("AI is on: A model on this Mac · qwen3:8b.");
    expect(aiStanding(status({ provider: "openai", model: "gpt-6.1-sol", last_ok: false, last_error: "the API key was rejected" })))
      .toBe("AI is on: OpenAI · gpt-6.1-sol. The last call failed: the API key was rejected.");
  });
  it("says why it is off", () => {
    expect(aiStanding(off("turned-off"))).toBe("AI is turned off.");
    expect(aiStanding(off("no-model", "local"))).toBe("AI is off: no model on this Mac is chosen.");
    expect(aiStanding(off("no-key", "gemini"))).toBe("AI is off: there’s no key for Gemini.");
  });
  it("says nothing before the digest has come", () => expect(aiStanding(undefined)).toBe(""));
});
