import { describe, expect, it, vi } from "vitest";
import type { PostBodies } from "../../lib/api/types";
import { createStore } from "../../lib/store";
import { aiKept, aiParts, CHOSEN_ELSEWHERE, keepKey, localFound, localMenu, localPicked, modelChoices, NEEDS, providerChanges, urlToSave, type LocalServer } from "./aiChoices";

describe("the AI tab", () => {
  it("holds only AI mode's switch while it is off", () => expect(aiParts(false)).toEqual(["mode"]));
  it("shows the connection once AI mode is on, then spending and what is written ahead", () => {
    expect(aiParts(true)).toEqual(["mode", "connection", "spending", "ahead"]);
  });
  it("says its settings are kept on this Mac and each key in the Keychain, never that they are in a file", () => {
    expect(aiKept(false, "openai", "")).toBe("Kept on this Mac, with each key in your login Keychain.");
    expect(aiKept(false, "openai", "")).not.toContain("mailmind.config.json");
  });
  it("says with AI mode on who the text of a message goes to, the provider chosen or nobody off this Mac", () => {
    expect(aiKept(true, "openai", "")).toBe("Kept on this Mac, with each key in your login Keychain. With AI on, the text of a message goes to OpenAI only when it is summarized, briefed, drafted or asked about; nothing else leaves this machine.");
    expect(aiKept(true, "gemini", "")).toContain("goes to Google only");
    expect(aiKept(true, "local", "")).toBe("Kept on this Mac, with each key in your login Keychain. With a model on this Mac, the text of a message never leaves this Mac.");
    expect(aiKept(true, "compatible", "http://127.0.0.1:8080/v1")).toContain("never leaves this Mac");
  });
});

const OLLAMA: LocalServer = { name: "Ollama", url: "http://127.0.0.1:11434/v1", models: ["llama3.3:latest", "qwen3:8b"] };
const STUDIO: LocalServer = { name: "LM Studio", url: "http://127.0.0.1:1234/v1", models: ["qwen3:8b"] };
const options = (m: ReturnType<typeof localMenu>) => [...(m.first ? [m.first.label] : []), ...m.groups.flatMap((g) => g.models.map((o) => `${g.name}: ${o.label}`))];

describe("a model on this Mac", () => {
  it("offers each server found by name, in the finder's order, with its models", () => {
    const menu = localMenu([OLLAMA, STUDIO], "", "");
    expect(menu.groups.map((g) => [g.name, g.url])).toEqual([["Ollama", OLLAMA.url], ["LM Studio", STUDIO.url]]);
    expect(options(menu)).toEqual(["Choose a model", "Ollama: llama3.3:latest", "Ollama: qwen3:8b", "LM Studio: qwen3:8b"]);
    expect(menu.value).toBe("");
  });
  it("sets the server's URL and the model in one change, the URL \"\" where it is Ollama's, which a model on this Mac starts at", () => {
    const menu = localMenu([OLLAMA, STUDIO], "", "");
    const [ollamas, studios] = [menu.groups[0]!.models[1]!.value, menu.groups[1]!.models[0]!.value];
    expect(localPicked(ollamas)).toEqual({ ai_base_url: "", ai_model: "qwen3:8b" });
    expect(localPicked(studios)).toEqual({ ai_base_url: "http://127.0.0.1:1234/v1", ai_model: "qwen3:8b" });
  });
  it("shows the model chosen where the server at the URL saved offers it, and the same name at another server isn't it", () => {
    const studios = localMenu([OLLAMA, STUDIO], "http://127.0.0.1:1234/v1", "qwen3:8b");
    expect(studios.first).toBeNull();
    expect(localPicked(studios.value)).toEqual({ ai_base_url: "http://127.0.0.1:1234/v1", ai_model: "qwen3:8b" });
    expect(localPicked(localMenu([OLLAMA, STUDIO], "", "qwen3:8b").value)).toEqual({ ai_base_url: "", ai_model: "qwen3:8b" });
  });
  it("keeps a model chosen that no server found offers, first and on its own", () => {
    const menu = localMenu([OLLAMA], "http://localhost:8080/v1", "mistral-small");
    expect(menu.value).toBe(CHOSEN_ELSEWHERE);
    expect(options(menu)).toEqual(["mistral-small", "Ollama: llama3.3:latest", "Ollama: qwen3:8b"]);
    expect(localMenu([], "", "mistral-small").first).toEqual({ value: CHOSEN_ELSEWHERE, label: "mistral-small" });
  });
  it("says what it found, which server has nothing to chat with yet, and with none what to start", () => {
    expect(localFound([OLLAMA])).toBe("Running on this Mac: Ollama.");
    expect(localFound([OLLAMA, STUDIO, { name: "Jan", url: "http://127.0.0.1:1337/v1", models: [] }]))
      .toBe("Running on this Mac: Ollama, LM Studio and Jan. Jan has no model to chat with yet.");
    expect(localFound([])).toBe("No model server is running on this Mac. Start one, Ollama or LM Studio for example, and look again.");
  });
});

describe("choosing another provider", () => {
  it("sets its model and URL back to its own, as the server does", () => {
    expect(providerChanges("openai")).toEqual({ ai_provider: "openai", ai_model: "", ai_base_url: "" });
    expect(providerChanges("local")).toEqual({ ai_provider: "local", ai_model: "", ai_base_url: "" });
  });
  it("needs a key of Claude, OpenAI and Gemini, takes one from the others, and asks a URL only of those", () => {
    expect(Object.entries(NEEDS).filter(([, n]) => n.key === "optional").map(([p]) => p)).toEqual(["local", "compatible"]);
    expect(Object.entries(NEEDS).filter(([, n]) => n.url !== null).map(([p]) => p)).toEqual(["local", "compatible"]);
    expect(NEEDS.local.url).toBe("http://127.0.0.1:11434/v1");
    expect(NEEDS.compatible.url).toBe("");
  });
  it("says where each provider that needs a key makes one", () => {
    expect([NEEDS.anthropic.keysAt, NEEDS.openai.keysAt, NEEDS.gemini.keysAt]).toEqual(
      ["https://console.anthropic.com", "https://platform.openai.com/api-keys", "https://aistudio.google.com/apikey"]);
  });
});

describe("the model menu", () => {
  const values = (choices: { value: string }[]) => choices.map((c) => c.value);

  it("offers the provider's own first, by name, and the rest as listed", () => {
    const choices = modelChoices(["gpt-6-luna", "gpt-6.1-sol"], "gpt-6.1-sol", "");
    expect(choices).toEqual([{ value: "", label: "Default (gpt-6.1-sol)" }, { value: "gpt-6-luna", label: "gpt-6-luna" }]);
  });
  it("says Default alone before the provider has listed its models", () => {
    expect(modelChoices([], undefined, "")).toEqual([{ value: "", label: "Default" }]);
  });
  it("keeps a model chosen by name though it is the provider's own", () => {
    expect(values(modelChoices(["gpt-6-luna", "gpt-6.1-sol"], "gpt-6.1-sol", "gpt-6.1-sol"))).toEqual(["", "gpt-6-luna", "gpt-6.1-sol"]);
  });
  it("puts a model typed by hand, or one the list no longer names, right after the default", () => {
    expect(values(modelChoices(["gpt-6-luna"], "gpt-6.1-sol", "my-tuned-model"))).toEqual(["", "my-tuned-model", "gpt-6-luna"]);
    expect(values(modelChoices([], undefined, "my-tuned-model"))).toEqual(["", "my-tuned-model"]);
  });
  it("asks for a model where the provider has none of its own, until one is chosen", () => {
    expect(modelChoices(["llama3.3:latest", "qwen3:8b"], null, "")).toEqual([
      { value: "", label: "Choose a model" }, { value: "llama3.3:latest", label: "llama3.3:latest" }, { value: "qwen3:8b", label: "qwen3:8b" },
    ]);
    expect(values(modelChoices(["llama3.3:latest", "qwen3:8b"], null, "qwen3:8b"))).toEqual(["llama3.3:latest", "qwen3:8b"]);
  });
});

describe("a URL typed", () => {
  it("saves as the provider's own when it is the one filled in", () => {
    expect(urlToSave("http://127.0.0.1:11434/v1", "http://127.0.0.1:11434/v1")).toBe("");
    expect(urlToSave(" http://127.0.0.1:11434/v1/ ", "http://127.0.0.1:11434/v1")).toBe("");
  });
  it("saves as typed otherwise, without a slash at its end", () => {
    expect(urlToSave("http://127.0.0.1:11500/v1", "http://127.0.0.1:11434/v1")).toBe("http://127.0.0.1:11500/v1");
    expect(urlToSave("https://llm.example.test/v1/", "")).toBe("https://llm.example.test/v1");
    expect(urlToSave("   ", "")).toBe("");
  });
});

describe("a key typed", () => {
  const KEY = "sk-test-fictional-0123456789";

  it("leaves the field as it is sent, before any answer, and goes to be kept for the provider", async () => {
    const field = createStore(`  ${KEY} `);
    let heldWhileSent: string | null = null;
    const send = vi.fn((body: PostBodies["/api/ai/key"]) => {
      heldWhileSent = field.get();
      return Promise.resolve({ ok: true as const, kept: [body.provider] });
    });
    expect(await keepKey(field, "openai", send)).toEqual({ ok: true, kept: ["openai"] });
    expect(send).toHaveBeenCalledWith({ provider: "openai", key: KEY });
    expect(heldWhileSent).toBe("");
    expect(field.get()).toBe("");
  });
  it("is gone from the field when the server refuses it too", async () => {
    const field = createStore(KEY);
    const refused = new Error("refused");
    await expect(keepKey(field, "gemini", () => Promise.reject(refused))).rejects.toBe(refused);
    expect(field.get()).toBe("");
  });
  it("sends nothing for an empty field", async () => {
    const field = createStore("  ");
    const send = vi.fn();
    expect(await keepKey(field, "anthropic", send)).toBeNull();
    expect(send).not.toHaveBeenCalled();
    expect(field.get()).toBe("");
  });
});
