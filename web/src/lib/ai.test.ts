import { describe, expect, it } from "vitest";
import { aiOffered, aiSends, aiSendsInShort, aiStanding, draftAheadSends, offLine, PROVIDER_LABEL, PROVIDER_NAME, runsHere, sendsOf, weeklyReviewSends, whyOff, type Sends } from "./ai";
import type { AiProvider, AiStatus } from "./api/types";

const status = (over: Partial<AiStatus> = {}): AiStatus => ({
  engine: "api", provider: "anthropic", off: null, model: "claude-opus-5-5", background_model: "claude-haiku-5-5", cloud_model: false,
  key_from: "mailmind.config.json", last_ok: null, last_ts: 0, last_error: null, limit_micros: 15_000_000, spent_today_micros: 0, ...over,
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
  // What decides it, for `provider` at `baseUrl`: by default the chosen model is no cloud's and nothing is drafted ahead.
  const sends = (provider: AiProvider, baseUrl = "", over: Partial<Sends> = {}): Sends => ({ provider, baseUrl, model: "qwen3:8b", cloud: false, draftAhead: false, ...over });
  const SENDS = "With AI mode on, the sender, subject and opening text of each message in your Inbox go to Anthropic to be summarized, "
    + "automatically: the last 30 days’ as soon as you turn it on, then each new one. Those senders, subjects and summaries go again "
    + "to write the day’s brief and what came in since you looked. Whole conversations go only when you ask: for a summary or a "
    + "draft, with what you type into Ask, a search written as a sentence, or Rewrite. Attachments never leave this Mac.";
  const AHEAD = "With Draft replies ahead on, whole conversations of important mail go too, to draft replies before you open them.";
  const CLOUD = "gpt-oss:120b-cloud runs in Ollama’s cloud, so what is summarized goes to Ollama’s servers.";

  it("says in short, for the sync menu, that the Inbox's mail goes on its own and attachments never", () => {
    expect(aiSendsInShort(sends("anthropic"))).toBe("The sender, subject and opening text of each message in your Inbox go to Anthropic "
      + "automatically, and more when you ask for something. Attachments never leave this Mac.");
    expect(aiSendsInShort(sends("openai"))).toContain(" go to OpenAI automatically");
    expect(aiSendsInShort(sends("gemini"))).toContain(" go to Google automatically");
    expect(aiSendsInShort(sends("compatible", "https://llm.example.test/v1"))).toContain(" go to the service at the URL you gave automatically");
  });
  it("says in short that important mail's whole conversations go too while replies are drafted ahead", () => {
    expect(aiSendsInShort(sends("anthropic", "", { draftAhead: true }))).toBe("The sender, subject and opening text of each message in "
      + "your Inbox go to Anthropic automatically, and more when you ask for something. With Draft replies ahead on, whole "
      + "conversations of important mail go too. Attachments never leave this Mac.");
  });
  it("says in short that nothing leaves this Mac when the provider answers here", () => {
    expect(aiSendsInShort(sends("local"))).toBe("With a model on this Mac, nothing leaves this Mac.");
    expect(aiSendsInShort(sends("local", "http://localhost:1234/v1"))).toBe("With a model on this Mac, nothing leaves this Mac.");
    expect(aiSendsInShort(sends("compatible", "http://localhost:8080/v1"))).toBe("With the service on this Mac, nothing leaves this Mac.");
    expect(aiSendsInShort(sends("local", "", { draftAhead: true }))).toBe("With a model on this Mac, nothing leaves this Mac.");
  });
  it("says in short that a model here which runs in Ollama's cloud sends what is summarized there", () => {
    expect(aiSendsInShort(sends("local", "", { model: "gpt-oss:120b-cloud", cloud: true }))).toBe(CLOUD);
    expect(aiSendsInShort(sends("local", "", { model: "gpt-oss:120b-cloud", cloud: true, draftAhead: true })))
      .toBe(`${CLOUD} With Draft replies ahead on, whole conversations of important mail go too.`);
  });
  it("says before AI mode is on what of the Inbox goes to the company behind the provider, when, and that attachments never do", () => {
    expect(aiSends(sends("anthropic"))).toBe(SENDS);
    expect(aiSends(sends("openai"))).toContain(" go to OpenAI to be summarized");
    expect(aiSends(sends("gemini"))).toContain(" go to Google to be summarized");
    expect(aiSends(sends("compatible", "https://llm.example.test/v1"))).toContain(" go to the service at the URL you gave to be summarized");
  });
  it("adds that important mail's whole conversations go too while replies are drafted ahead", () => {
    expect(aiSends(sends("anthropic", "", { draftAhead: true }))).toBe(`${SENDS} ${AHEAD}`);
    expect(aiSends(sends("gemini", "", { draftAhead: true }))).toContain(" go to Google to be summarized");
  });
  it("says nothing leaves this Mac when the provider answers here, drafting ahead or not", () => {
    expect(aiSends(sends("local"))).toBe("With a model on this Mac, nothing leaves this Mac.");
    expect(aiSends(sends("local", "", { draftAhead: true }))).toBe("With a model on this Mac, nothing leaves this Mac.");
    expect(aiSends(sends("compatible", "http://localhost:8080/v1"))).toBe("With the service on this Mac, nothing leaves this Mac.");
    expect(aiSends(sends("openai", "http://127.0.0.1:8080/v1"))).toBe("With the service on this Mac, nothing leaves this Mac.");
  });
  it("says a model here that runs in Ollama's cloud sends what is summarized to Ollama's servers, and drafting ahead more", () => {
    expect(aiSends(sends("local", "", { model: "gpt-oss:120b-cloud", cloud: true }))).toBe(CLOUD);
    expect(aiSends(sends("compatible", "http://localhost:11434/v1", { model: "gpt-oss:120b-cloud", cloud: true }))).toBe(CLOUD);
    expect(aiSends(sends("local", "", { model: "gpt-oss:120b-cloud", cloud: true, draftAhead: true }))).toBe(`${CLOUD} ${AHEAD}`);
  });
  it("names the model chosen when the cloud's model has no name yet, as the Mac app does", () => {
    expect(aiSends(sends("local", "", { model: "", cloud: true }))).toBe("The model chosen runs in Ollama’s cloud, so what is summarized goes to Ollama’s servers.");
  });
  it("says under the draft-ahead switch what drafting ahead sends, and that nothing leaves with a model that runs on this Mac", () => {
    expect(draftAheadSends(sends("anthropic"))).toBe("On, whole conversations of important mail go to Anthropic to draft them.");
    expect(draftAheadSends(sends("compatible", "https://llm.example.test/v1"))).toBe("On, whole conversations of important mail go to the service at the URL you gave to draft them.");
    expect(draftAheadSends(sends("local", "", { model: "gpt-oss:120b-cloud", cloud: true }))).toBe("On, whole conversations of important mail go to Ollama’s servers to draft them.");
    expect(draftAheadSends(sends("local"))).toBe("On, they are drafted on this Mac, and nothing leaves it.");
    expect(draftAheadSends(sends("compatible", "http://localhost:8080/v1"))).toBe("On, they are drafted on this Mac, and nothing leaves it.");
  });
  it("says under the weekly review's switch what goes each Monday beyond the Inbox, and that nothing leaves with a model here", () => {
    const REVIEW = "On, each Monday the senders, subjects and summaries of mail that needs you, the subjects and recipients of threads "
      + "you’re waiting on, and the names of the week’s meetings go to";
    expect(weeklyReviewSends(sends("anthropic"))).toBe(`${REVIEW} Anthropic to write it.`);
    expect(weeklyReviewSends(sends("gemini"))).toBe(`${REVIEW} Google to write it.`);
    expect(weeklyReviewSends(sends("compatible", "https://llm.example.test/v1"))).toBe(`${REVIEW} the service at the URL you gave to write it.`);
    expect(weeklyReviewSends(sends("local", "", { model: "gpt-oss:120b-cloud", cloud: true }))).toBe(`${REVIEW} Ollama’s servers to write it.`);
    expect(weeklyReviewSends(sends("local"))).toBe("On, it is written on this Mac, and nothing leaves it.");
    expect(weeklyReviewSends(sends("compatible", "http://localhost:8080/v1"))).toBe("On, it is written on this Mac, and nothing leaves it.");
  });
  it("is read from the digest for the provider in force, and from the preferences for one chosen since", () => {
    const p = { ai_provider: "local" as const, ai_base_url: "", ai_model: "gpt-oss:120b-cloud", draft_ahead: true };
    expect(sendsOf(status({ provider: "local", model: "gpt-oss:120b-cloud", cloud_model: true }), p))
      .toEqual({ provider: "local", baseUrl: "", model: "gpt-oss:120b-cloud", cloud: true, draftAhead: true });
    expect(sendsOf(off("turned-off", "local"), p).model).toBe("gpt-oss:120b-cloud");       // the server names no model while AI is off
    expect(sendsOf(status({ cloud_model: true }), p, "local").cloud).toBe(false);          // what the server said was of another provider's
    expect(sendsOf(undefined, p)).toMatchObject({ provider: "local", cloud: false });
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
