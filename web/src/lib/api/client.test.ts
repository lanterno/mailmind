import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AI_OVER_HEADER, ApiError, refusalOf, request } from "./client";

const answer = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const problem = (status: number, body: object) =>
  new Response(JSON.stringify({ status, ...body }), { status, headers: { "Content-Type": "application/problem+json" } });

describe("request", () => {
  const fetchMock = vi.fn<typeof fetch>();
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("document", { querySelector: () => ({ content: "tok" }) });
  });
  afterEach(() => vi.unstubAllGlobals());
  const sent = (call: number) => new Headers(fetchMock.mock.calls[call]![1]!.headers);

  it("sends the token on every request and gives back the JSON", async () => {
    fetchMock.mockResolvedValue(answer(200, { ok: true }));
    await expect(request("/api/stats")).resolves.toEqual({ ok: true });
    expect(sent(0).get("X-CSRF-Token")).toBe("tok");
  });

  it("raises the server's error with what kind it is", async () => {
    fetchMock.mockResolvedValue(problem(409, { type: "/problems/ai-off", title: "AI is off", detail: "AI is off", ai_off: true }));
    const e = await request("/api/x").catch((x: unknown) => x);
    expect(e).toBeInstanceOf(ApiError);
    expect(e).toMatchObject({ message: "AI is off", status: 409, type: "/problems/ai-off", aiOff: true, overLimit: false });
  });

  it("takes no words from an answer that isn't a problem document", async () => {
    fetchMock.mockResolvedValue(answer(409, { ok: false, error: "the old shape" }));
    await expect(request("/api/x")).rejects.toMatchObject({ message: "HTTP 409", detail: "", type: "about:blank" });
  });

  it("reads a problem document: its detail as the message, its type, and what the page acts on", async () => {
    fetchMock.mockResolvedValue(problem(409, { type: "/problems/conflict", title: "Conflict", detail: "This draft changed.", conflict: true, version: 4 }));
    const e = await request("/api/x").catch((x: unknown) => x);
    expect(e).toBeInstanceOf(ApiError);
    expect(e).toMatchObject({ message: "This draft changed.", detail: "This draft changed.", status: 409, type: "/problems/conflict", conflict: true, busy: false });
    expect((e as ApiError).problem["version"]).toBe(4);
  });

  it("reads each flag a problem may carry", async () => {
    fetchMock.mockResolvedValue(problem(409, { type: "/problems/busy", busy: true, read_only: true, retry: true, ai_off: true }));
    await expect(request("/api/x")).rejects.toMatchObject({ busy: true, readOnly: true, retry: true, aiOff: true, overLimit: false, detail: "", message: "HTTP 409" });
  });

  it("names the status when the answer isn't JSON, or isn't an object", async () => {
    fetchMock.mockResolvedValue(new Response("nope", { status: 500 }));
    await expect(request("/api/x")).rejects.toMatchObject({ message: "HTTP 500", status: 500, type: "about:blank", detail: "" });
    fetchMock.mockResolvedValue(answer(404, ["not", "one"]));
    await expect(request("/api/x")).rejects.toMatchObject({ message: "HTTP 404", status: 404, problem: {} });
  });

  it("takes a 4xx for the server's no, and anything else for a failure the page words", () => {
    expect(refusalOf(new ApiError(404, { detail: "not found" }))?.detail).toBe("not found");
    expect(refusalOf(new ApiError(409, {}))).not.toBeNull();
    for (const e of [new ApiError(500, { detail: "send failed" }), new ApiError(502, {}), new Error("offline"), null]) expect(refusalOf(e)).toBeNull();
  });

  it("at the limit, a request nobody asked about gets the error", async () => {
    fetchMock.mockResolvedValue(problem(402, { type: "/problems/over-limit", detail: "over", over_limit: true }));
    await expect(request("/api/x")).rejects.toMatchObject({ status: 402, overLimit: true });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("at the limit, asks with what a problem document says, and a yes sends it once more", async () => {
    const over = { type: "/problems/over-limit", detail: "today's AI spending has reached the daily limit", over_limit: true, spent_micros: 15, limit_micros: 10 };
    fetchMock.mockResolvedValueOnce(problem(402, over)).mockResolvedValueOnce(answer(200, { ok: true }));
    const ask = vi.fn(async () => true);
    await expect(request("/api/x", { askOverLimit: ask })).resolves.toEqual({ ok: true });
    expect(ask).toHaveBeenCalledWith({ status: 402, ...over });
    expect(sent(1).get(AI_OVER_HEADER)).toBe("allow");
  });

  it("at the limit, a yes sends it once more with the header", async () => {
    fetchMock.mockResolvedValueOnce(answer(402, { over_limit: true, spent_micros: 15 })).mockResolvedValueOnce(answer(200, { ok: 1 }));
    const ask = vi.fn(async () => true);
    await expect(request("/api/x", { askOverLimit: ask })).resolves.toEqual({ ok: 1 });
    expect(ask).toHaveBeenCalledWith({ over_limit: true, spent_micros: 15 });
    expect(sent(1).get(AI_OVER_HEADER)).toBe("allow");
  });

  it("at the limit, a no is the error, and a second 402 doesn't ask again", async () => {
    fetchMock.mockImplementation(async () => answer(402, { over_limit: true }));
    await expect(request("/api/x", { askOverLimit: async () => false })).rejects.toMatchObject({ overLimit: true });
    fetchMock.mockClear();
    const ask = vi.fn(async () => true);
    await expect(request("/api/x", { askOverLimit: ask })).rejects.toMatchObject({ overLimit: true });
    expect(ask).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
