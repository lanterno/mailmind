import type { QueryClient } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../lib/api/client";
import type { Plan } from "./ask";

const the = vi.hoisted(() => ({ post: vi.fn(), announce: vi.fn(), toast: vi.fn(), offerUndo: vi.fn(), readAgain: vi.fn(), askOverLimit: vi.fn() }));
vi.mock("../../lib/api/client", async (real) => ({ ...(await real<typeof import("../../lib/api/client")>()), post: the.post }));
vi.mock("../../lib/toast", () => ({ announce: the.announce, toast: the.toast }));
vi.mock("../../lib/overLimit", () => ({ askOverLimit: the.askOverLimit }));
vi.mock("../mailbox", () => ({ offerUndo: the.offerUndo, readAgain: the.readAgain, undoGroup: () => "ug-1" }));

// An answer the test hands over when it chooses, to put two requests in flight at once.
function later<T>() {
  let give!: (value: T) => void, fail!: (e: unknown) => void;
  const promise = new Promise<T>((resolve, reject) => {
    give = resolve;
    fail = reject;
  });
  return { promise, give, fail };
}
const aiOff = () => new ApiError(409, { detail: "AI is off", ai_off: true });
const preview = (over: object = {}) => ({ ok: true, intent: "act", verb: "archive", count: 12, cap: 500, capped: false, filter: "from:shop", plan: "p1", sample: [], snooze_preset: "tomorrow", ...over }) as Plan;

let ask: typeof import("./ask");
beforeEach(async () => {
  Object.values(the).forEach((fn) => fn.mockReset());
  vi.resetModules();
  ask = await import("./ask");
});

describe("asking your inbox", () => {
  it("shows it's working, then the answer, and says how many sources it cites", async () => {
    const answer = later<object>();
    the.post.mockReturnValue(answer.promise);
    const asked = ask.ask("When is the flight?");
    expect(ask.asking.get()).toEqual({ question: "When is the flight?", state: "loading" });
    answer.give({ answer: "Friday", sources: [{ key: 1 }] });
    await asked;
    expect(ask.asking.get()).toMatchObject({ state: "answer", answer: { answer: "Friday" } });
    expect(the.announce).toHaveBeenCalledWith("Answer ready — 1 cited source");
    expect(the.post).toHaveBeenCalledWith("/api/ai/ask", { account: "all", question: "When is the flight?" }, { askOverLimit: the.askOverLimit });
  });
  it("counts sources in the plural, and none", async () => {
    the.post.mockResolvedValueOnce({ answer: "", sources: [{}, {}] }).mockResolvedValueOnce({ answer: "" });
    await ask.ask("a");
    await ask.ask("b");
    expect(the.announce.mock.calls).toEqual([["Answer ready — 2 cited sources"], ["Answer ready — 0 cited sources"]]);
  });
  it("shows only the newest question's answer, though an older one comes back last", async () => {
    const first = later<object>(), second = later<object>();
    the.post.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const one = ask.ask("first"), two = ask.ask("second");
    second.give({ answer: "two", sources: [] });
    await two;
    first.give({ answer: "one", sources: [] });
    await one;
    expect(ask.asking.get()).toMatchObject({ question: "second", answer: { answer: "two" } });
  });
  it("says the AI is off when it is, and that the ask failed otherwise", async () => {
    the.post.mockRejectedValueOnce(aiOff()).mockRejectedValueOnce(new Error("down"));
    await ask.ask("a");
    expect(ask.asking.get()).toEqual({ question: "a", state: "error", error: "AI is off" });
    await ask.ask("b");
    expect(ask.asking.get()).toEqual({ question: "b", state: "error", error: "ask failed" });
  });
  it("once stopped, shows nothing of an answer still on its way, nor of its failure", async () => {
    const answer = later<object>(), failing = later<object>();
    the.post.mockReturnValueOnce(answer.promise).mockReturnValueOnce(failing.promise);
    const asked = ask.ask("a");
    ask.stopAsking();
    answer.give({ answer: "late", sources: [] });
    await asked;
    expect(ask.asking.get()).toBeNull();
    const failed = ask.ask("b");
    ask.stopAsking();
    failing.fail(new Error("down"));
    await failed;
    expect(ask.asking.get()).toBeNull();
    expect(the.announce).not.toHaveBeenCalled();
  });
});

describe("planning a bulk action", () => {
  it("shows what it would do for review, and does nothing yet", async () => {
    the.post.mockResolvedValue(preview());
    const search = vi.fn();
    await ask.runDo("archive the shop mail", search);
    expect(ask.doing.get()).toEqual({ state: "plan", cmd: "archive the shop mail", plan: preview(), armed: false });
    expect(the.announce).toHaveBeenCalledWith("Archive 12 messages? Review and confirm.");
    expect(the.post).toHaveBeenCalledTimes(1);
    expect(search).not.toHaveBeenCalled();
  });
  it("shows a plan that acts on nothing as the search it is", async () => {
    the.post.mockResolvedValue({ ok: true, intent: "search", filter: "  from:bo  " });
    const search = vi.fn();
    await ask.runDo("mail from bo", search);
    expect(search).toHaveBeenCalledWith("from:bo");
    expect(ask.doing.get()).toBeNull();
  });
  it("says so when it found nothing to search for", async () => {
    the.post.mockResolvedValue({ ok: true, intent: "search", filter: " " });
    const search = vi.fn();
    await ask.runDo("hmm", search);
    expect(ask.doing.get()).toEqual({ state: "nofilter", cmd: "hmm" });
    expect(search).not.toHaveBeenCalled();
  });
  it("says the AI is off when it is, and that the planner failed otherwise", async () => {
    the.post.mockRejectedValueOnce(aiOff()).mockRejectedValueOnce(new Error("down"));
    await ask.runDo("a", vi.fn());
    expect(ask.doing.get()).toEqual({ state: "error", cmd: "a", error: "AI is off" });
    await ask.runDo("b", vi.fn());
    expect(ask.doing.get()).toEqual({ state: "error", cmd: "b", error: "planner failed" });
  });
  it("once stopped, neither shows a plan still on its way nor runs its search", async () => {
    const plan = later<object>();
    the.post.mockReturnValue(plan.promise);
    const search = vi.fn();
    const planning = ask.runDo("mail from bo", search);
    ask.stopDoing();
    plan.give({ ok: true, intent: "search", filter: "from:bo" });
    await planning;
    expect(ask.doing.get()).toBeNull();
    expect(search).not.toHaveBeenCalled();
  });
});

describe("carrying out a plan", () => {
  const qc = {} as QueryClient;

  it("does what the preview froze, by its id under one undo group, and offers Undo", async () => {
    the.post.mockResolvedValue({ ok: true, verb: "archive", count: 12, failed: 0, gone: 0, queued: 0, undoable: true });
    const clear = vi.fn();
    await ask.confirmPlan(qc, preview(), clear);
    expect(the.post).toHaveBeenCalledWith("/api/ai/do-confirm", { plan: "p1", undo_group: "ug-1" });
    expect(the.toast.mock.calls).toEqual([["Archive 12…"], ["Archived 12"]]);
    expect(the.offerUndo).toHaveBeenCalledWith("Archived 12");
    expect(clear).toHaveBeenCalled();
    expect(the.readAgain).toHaveBeenCalledWith(qc);
    expect(ask.doing.get()).toBeNull();
  });
  it("says how many failed or had moved, and offers no Undo it can't keep", async () => {
    the.post.mockResolvedValue({ ok: true, verb: "star", count: 9, failed: 2, gone: 1, queued: 0, undoable: false });
    await ask.confirmPlan(qc, preview({ verb: "star", count: 12 }), vi.fn());
    expect(the.toast).toHaveBeenLastCalledWith("Updated 9, 2 failed, 1 had moved");
    expect(the.offerUndo).not.toHaveBeenCalled();
  });
  it("counts up to the cap when the plan was capped", async () => {
    the.post.mockResolvedValue({ ok: true, verb: "trash", count: 500, failed: 0, gone: 0, queued: 0, undoable: true });
    await ask.confirmPlan(qc, preview({ verb: "trash", count: 900, capped: true }), vi.fn());
    expect(the.toast.mock.calls[0]).toEqual(["Delete 500…"]);
  });
  it("says to run it again when the preview has expired, and to check otherwise, and clears the box either way", async () => {
    the.post.mockRejectedValueOnce(new ApiError(410, {})).mockRejectedValueOnce(new Error("down"));
    const clear = vi.fn();
    await ask.confirmPlan(qc, preview(), clear);
    expect(the.toast).toHaveBeenLastCalledWith("That preview has expired. Run the command again.");
    await ask.confirmPlan(qc, preview(), clear);
    expect(the.toast).toHaveBeenLastCalledWith("Couldn’t finish that. Check what was done before trying again.");
    expect(clear).toHaveBeenCalledTimes(2);
    expect(the.readAgain).toHaveBeenCalledTimes(2);
  });
  it("does nothing with a plan that is a search", async () => {
    await ask.confirmPlan(qc, { ok: true, intent: "search", filter: "x" } as Plan, vi.fn());
    expect(the.post).not.toHaveBeenCalled();
  });
});

describe("a sentence in the search box", () => {
  it("becomes the search the planner reads in it", async () => {
    the.post.mockResolvedValue({ ok: true, intent: "search", filter: " from:bo after:2026-10-01 " });
    const show = vi.fn();
    await ask.translateProse("mail from bo since October", show);
    expect(show).toHaveBeenCalledWith("from:bo after:2026-10-01");
    expect(the.post).toHaveBeenCalledWith("/api/ai/do-plan", { cmd: "mail from bo since October" });
    expect(ask.translating.get()).toBeNull();
  });
  it("costs nothing the second time Enter is pressed on the same words", async () => {
    the.post.mockResolvedValue({ ok: true, intent: "search", filter: "from:bo" });
    const show = vi.fn();
    await ask.translateProse("mail from bo", show);
    await ask.translateProse("mail from bo", show);
    expect(the.post).toHaveBeenCalledTimes(1);
    expect(show.mock.calls).toEqual([["from:bo"], ["from:bo"]]);
  });
  it("isn't asked twice while the first is on its way", async () => {
    const plan = later<object>();
    the.post.mockReturnValue(plan.promise);
    const first = ask.translateProse("mail from bo", vi.fn());
    expect(ask.translating.get()).toBe("mail from bo");
    await ask.translateProse("mail from bo", vi.fn());
    plan.give({ filter: "from:bo" });
    await first;
    expect(the.post).toHaveBeenCalledTimes(1);
  });
  it("leaves the literal search standing when the planner fails, finds nothing, or gives the words back", async () => {
    the.post.mockRejectedValueOnce(new Error("down")).mockResolvedValueOnce({ filter: "" }).mockResolvedValueOnce({ filter: "Invoices" });
    const show = vi.fn();
    await ask.translateProse("a", show);
    await ask.translateProse("b", show);
    await ask.translateProse("invoices", show);
    expect(show).not.toHaveBeenCalled();
    expect(ask.translating.get()).toBeNull();
  });
  it("isn't translated again once someone took the translation back", async () => {
    the.post.mockResolvedValue({ filter: "from:bo" });
    await ask.translateProse("mail from bo", vi.fn());
    ask.keepLiteral("mail from bo");
    const show = vi.fn();
    await ask.translateProse("mail from bo", show);
    expect(show).not.toHaveBeenCalled();
    expect(the.post).toHaveBeenCalledTimes(1);
  });
  it("once stopped, shows nothing of a translation still on its way", async () => {
    const plan = later<object>();
    the.post.mockReturnValue(plan.promise);
    const show = vi.fn();
    const going = ask.translateProse("mail from bo", show);
    ask.stopTranslating();
    expect(ask.translating.get()).toBeNull();
    plan.give({ filter: "from:bo" });
    await going;
    expect(show).not.toHaveBeenCalled();
  });
});
