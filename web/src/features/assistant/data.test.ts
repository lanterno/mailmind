import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../lib/api/client";
import { cleanup, renderHook, testClient, waitFor } from "../../lib/testing/renderHook";
import { LIMIT, useBrief, useReview, useSide, whatWentWrong } from "./data";

const the = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), askOverLimit: vi.fn() }));
vi.mock("../../lib/api/client", async (real) => ({ ...(await real<typeof import("../../lib/api/client")>()), get: the.get, post: the.post }));
vi.mock("../../lib/overLimit", () => ({ askOverLimit: the.askOverLimit }));

const brief = { headline: "A calm day", groups: [] };
const posted = () => the.post.mock.calls.map(([path, , options]) => [path, options?.askOverLimit === the.askOverLimit]);

beforeEach(() => [the.get, the.post].forEach((fn) => fn.mockReset()));
afterEach(() => cleanup());

describe("what went wrong, beside Try again", () => {
  it("names the daily limit, so the brief can offer to go over it", () => {
    expect(whatWentWrong(new ApiError(402, { over_limit: true }))).toBe(LIMIT);
  });
  it("is nothing when the AI is off, which the brief says in its own words", () => {
    expect(whatWentWrong(new ApiError(409, { ai_off: true }))).toBeNull();
    expect(whatWentWrong(null)).toBeNull();
  });
  it("is that the server couldn't be reached, or what a failure said of itself", () => {
    expect(whatWentWrong(new ApiError(502, {}))).toBe("couldn’t reach the server");
    expect(whatWentWrong(new Error("The brief timed out"))).toBe("The brief timed out");
    expect(whatWentWrong(new Error(""))).toBe("couldn’t reach the server");
    expect(whatWentWrong("odd")).toBe("couldn’t reach the server");
  });
});

describe("today's brief", () => {
  const reading = (ai: boolean | undefined, client = testClient()) => renderHook((on: boolean | undefined) => useBrief(on), { props: ai, client });

  it("waits to know whether the AI is on before it reads", async () => {
    const r = await reading(undefined);
    expect(the.get).not.toHaveBeenCalled();
    expect(r.result.brief).toBeUndefined();
  });
  it("is written on opening the Inbox when none is stored, never at the daily limit", async () => {
    the.get.mockResolvedValue({ missing: true });
    the.post.mockResolvedValue(brief);
    const r = await reading(true);
    await waitFor(() => expect(r.result.brief).toEqual(brief));
    expect(posted()).toEqual([["/api/briefing", false]]);
  });
  it("is what is stored, written by nobody, with the AI off", async () => {
    the.get.mockResolvedValue({ missing: true });
    const r = await reading(false);
    await waitFor(() => expect(r.result.brief).toEqual({ missing: true }));
    expect(the.post).not.toHaveBeenCalled();
  });
  it("is written again on a click, which may go over the limit, and the side of it is read again", async () => {
    the.get.mockResolvedValue(brief);
    const client = testClient();
    vi.spyOn(client, "invalidateQueries");
    const r = await reading(true, client);
    await waitFor(() => expect(r.result.brief).toEqual(brief));
    the.post.mockResolvedValue({ ...brief, headline: "Two things need you" });
    await act(async () => r.result.write(true));
    await waitFor(() => expect(r.result.brief).toMatchObject({ headline: "Two things need you" }));
    expect(posted()).toEqual([["/api/briefing/refresh", true]]);
    expect(client.invalidateQueries).toHaveBeenCalledWith({ queryKey: ["briefSide"] });
  });
  it("says it's at the limit when a click is refused for it", async () => {
    the.get.mockResolvedValue({ missing: true });
    the.post.mockRejectedValue(new ApiError(402, { over_limit: true }));
    const r = await reading(false);
    await waitFor(() => expect(r.result.writing).toBe(false));
    await act(async () => r.result.write(false));
    await waitFor(() => expect(r.result.error).toBe(LIMIT));
    expect(posted()).toEqual([["/api/briefing", true]]);
  });
});

describe("the brief's side", () => {
  it("shows what came of one half when the other fails", async () => {
    the.get.mockImplementation(async (path: string) => {
      if (path === "/api/upcoming") throw new Error("down");
      return { items: [{ key: 1 }] };
    });
    const r = await renderHook(() => useSide(), { client: testClient() });
    await waitFor(() => expect(r.result.isSuccess).toBe(true));
    expect(r.result.data).toEqual({ upcoming: [], waiting: [{ key: 1 }] });
  });
});

describe("the weekly review", () => {
  const review = { week: "2026-11-02", summary: "A busy week" };

  it("reads nothing until it is opened", async () => {
    await renderHook(() => useReview(false), { client: testClient() });
    expect(the.get).not.toHaveBeenCalled();
  });
  it("is written on opening when none is stored, since you asked by opening it", async () => {
    the.get.mockResolvedValue({ missing: true });
    the.post.mockResolvedValue(review);
    const r = await renderHook(() => useReview(true), { client: testClient() });
    expect(r.result.loading).toBe(true);
    await waitFor(() => expect(r.result.review).toEqual(review));
    expect(r.result.loading).toBe(false);
    expect(posted()).toEqual([["/api/review", true]]);
  });
  it("shows nothing of the old one while it is written again", async () => {
    the.get.mockResolvedValue(review);
    const r = await renderHook(() => useReview(true), { client: testClient() });
    await waitFor(() => expect(r.result.review).toEqual(review));
    let finish!: (v: object) => void;
    the.post.mockReturnValue(new Promise((done) => void (finish = done)));
    await act(async () => r.result.again());
    await waitFor(() => expect(r.result.review).toBeUndefined());
    expect(r.result.loading).toBe(true);
    await act(async () => finish({ ...review, summary: "A quieter week" }));
    await waitFor(() => expect(r.result.review).toMatchObject({ summary: "A quieter week" }));
    expect(posted()).toEqual([["/api/review/refresh", true]]);
  });
});
