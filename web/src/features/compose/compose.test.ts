import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LIMITS } from "../../lib/api/limits.gen";
import { testClient } from "../../lib/testing/renderHook";
import type { Env } from "./machine";

// The page as the composer's machine reaches it: the machine is machine.test.ts's, so here it only takes what it is given.
const the = vi.hoisted(() => ({ env: undefined as Env | undefined, get: vi.fn(), post: vi.fn(), go: vi.fn(), askOverLimit: vi.fn(), tone: "warm" }));
vi.mock("./machine", () => ({ createComposing: (env: Env) => ((the.env = env), { isOpen: () => false }) }));
vi.mock("../../lib/api/client", () => ({ get: the.get, post: the.post }));
vi.mock("../../lib/overLimit", () => ({ askOverLimit: the.askOverLimit }));
vi.mock("../../lib/toast", () => ({ announce: vi.fn(), toast: vi.fn() }));
vi.mock("../../app/route", async (real) => ({ ...(await real<typeof import("../../app/route")>()), go: the.go }));
vi.mock("../../lib/prefs", () => ({ pref: () => ({ get: () => the.tone, set: (t: string) => void (the.tone = t) }) }));

const A = "ada@example.test", W = "ada@work.example.test";
const env = () => the.env!;
let compose: typeof import("./compose");
const client = testClient();
const where = (pathname: string, search = "") => vi.stubGlobal("location", { pathname, search });

beforeEach(async () => {
  [the.get, the.post, the.go, the.askOverLimit].forEach((fn) => fn.mockReset());
  the.tone = "warm";
  client.clear();
  where("/all");
  vi.resetModules();
  compose = await import("./compose");
  compose.bindComposer(client);
});
afterEach(() => vi.unstubAllGlobals());

describe("who you are", () => {
  it("is every account and every address it sends as, whatever their case", () => {
    client.setQueryData(["digest"], { accounts: [A], send_accounts: ["Ada+news@Example.test", W] });
    expect(compose.myAddresses().has(A)).toBe(true);
    expect(compose.myAddresses().has(W)).toBe(true);
    expect(compose.myAddresses().has("ada+news@example.test")).toBe(true);
    expect(env().mine()).toEqual(compose.myAddresses());
  });
  it("is nobody before the digest has come", () => expect(compose.myAddresses().size).toBe(0));
});

describe("sending from", () => {
  it("the addresses the digest says you send as, else your accounts", () => {
    client.setQueryData(["digest"], { accounts: [A], send_accounts: [W] });
    expect(env().senders()).toEqual([W]);
    client.setQueryData(["digest"], { accounts: [A], send_accounts: [] });
    expect(env().senders()).toEqual([A]);
  });
  it("the account of the folder or the search you are in, and none on All inboxes", () => {
    where(`/m/${encodeURIComponent(W)}/INBOX`);
    expect(env().current()).toBe(W);
    where("/search", `?q=bills&account=${encodeURIComponent(A)}`);
    expect(env().current()).toBe(A);
    where("/all");
    expect(env().current()).toBeUndefined();
  });
  it("signs with the address's own signature, else the default, else nothing", () => {
    expect(env().signature(A)).toBe("");
    client.setQueryData(["digest"], { accounts: [A], signatures: { [W]: "— Ada at work" }, signature_default: "— Ada" });
    expect(env().signature(W)).toBe("— Ada at work");
    expect(env().signature(A)).toBe("— Ada");
  });
  it("drafts in the tone last chosen", () => {
    compose.setDraftTone("formal");
    expect(env().tone()).toBe("formal");
  });
  it("holds a person to the server's own limits", () => expect(env().limits).toEqual(LIMITS));
});

describe("asking the server", () => {
  it("lets only what a person started go over the day's AI limit", async () => {
    the.post.mockResolvedValue({ ok: true });
    await env().post("/api/ai/draft-reply", {}, true);
    await env().post("/api/draft", {});
    expect(the.post.mock.calls.map(([, , options]) => options)).toEqual([{ askOverLimit: the.askOverLimit }, {}]);
  });
});

describe("what else has to know", () => {
  const reread = () => vi.mocked(client.invalidateQueries).mock.calls.map(([f]) => (f!.queryKey as string[])[0]);
  beforeEach(() => void vi.spyOn(client, "invalidateQueries"));

  it("a draft saved reads the lists again, and the outbox its own", () => {
    env().draftsChanged();
    env().outboxChanged();
    env().foldersChanged();
    expect(reread()).toEqual(["messages", "folders", "outbox", "pending", "folders"]);
  });
  it("a reply sent reads its conversation again and forgets the reply written ahead for it", () => {
    const forget = vi.fn();
    compose.onForgetDraftAhead(forget);
    env().sent(42);
    expect(reread()).toEqual(["messages", "thread", "folders"]);
    expect(forget).toHaveBeenCalledWith(42);
    env().sent(null);
    expect(forget).toHaveBeenCalledTimes(1);
  });
  it("a draft gone while it is open closes it, and leaves another copy open", () => {
    where(`/m/${encodeURIComponent(A)}/Drafts`, "?key=5&folder=Drafts&uid=50");
    env().draftGone({ key: 5, folder: "INBOX", uid: 50 });
    expect(the.go).not.toHaveBeenCalled();
    env().draftGone({ key: 5, folder: "Drafts", uid: 51 });
    expect(the.go).toHaveBeenCalledWith({ source: { kind: "folder", account: A, folder: "Drafts" }, opened: null });
  });
});
