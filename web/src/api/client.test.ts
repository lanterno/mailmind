import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AI_OVER_HEADER, ApiError, request } from "./client";

const answer = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

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
    fetchMock.mockResolvedValue(answer(409, { error: "AI is off", ai_off: true }));
    const e = await request("/api/x").catch((x: unknown) => x);
    expect(e).toBeInstanceOf(ApiError);
    expect(e).toMatchObject({ message: "AI is off", status: 409, aiOff: true, overLimit: false });
  });

  it("names the status when the answer isn't JSON", async () => {
    fetchMock.mockResolvedValue(new Response("nope", { status: 500 }));
    await expect(request("/api/x")).rejects.toMatchObject({ message: "HTTP 500", status: 500 });
  });

  it("at the limit, a request nobody asked about gets the error", async () => {
    fetchMock.mockResolvedValue(answer(402, { error: "over", over_limit: true }));
    await expect(request("/api/x")).rejects.toMatchObject({ status: 402, overLimit: true });
    expect(fetchMock).toHaveBeenCalledTimes(1);
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
