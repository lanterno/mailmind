import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../lib/api/client";
import { cleanup, renderHook, testClient, waitFor } from "../../lib/testing/renderHook";
import { useTaskChanges, useTasks } from "./tasks";

const the = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), toast: vi.fn() }));
vi.mock("../../lib/api/client", async (real) => ({ ...(await real<typeof import("../../lib/api/client")>()), get: the.get, post: the.post }));
vi.mock("../../lib/toast", () => ({ toast: the.toast }));

const task = { id: 3, text: "Send the invoice to bo@example.test", done: false };

beforeEach(() => [the.get, the.post, the.toast].forEach((fn) => fn.mockReset()));
afterEach(() => cleanup());

it("lists the tasks someone confirmed", async () => {
  the.get.mockResolvedValue({ items: [task] });
  const r = await renderHook(() => useTasks(), { client: testClient() });
  await waitFor(() => expect(r.result.data).toEqual([task]));
  expect(the.get).toHaveBeenCalledWith("/api/tasks");
});

describe("changing a task", () => {
  async function changing() {
    const client = testClient();
    vi.spyOn(client, "invalidateQueries");
    const r = await renderHook(() => useTaskChanges(), { client });
    const reread = () => waitFor(() => expect(client.invalidateQueries).toHaveBeenCalledWith({ queryKey: ["tasks"] }));
    return { r, reread };
  }

  it("marks one done, or not, and reads the list again", async () => {
    the.post.mockResolvedValue({ ok: true });
    const { r, reread } = await changing();
    await act(async () => r.result.mark(3, true));
    await reread();
    expect(the.post).toHaveBeenCalledWith("/api/tasks/done", { id: 3, done: true });
    expect(the.toast).not.toHaveBeenCalled();
  });
  it("removes one, and reads the list again", async () => {
    the.post.mockResolvedValue({ ok: true });
    const { r, reread } = await changing();
    await act(async () => r.result.remove(3));
    await reread();
    expect(the.post).toHaveBeenCalledWith("/api/tasks/remove", { id: 3 });
  });
  it("says why the server refused, and reads the list again anyway", async () => {
    the.post.mockRejectedValue(new ApiError(404, { detail: "That task is gone." }));
    const { r, reread } = await changing();
    await act(async () => r.result.remove(3));
    await reread();
    expect(the.toast).toHaveBeenCalledWith("That task is gone.");
  });
  it("says it couldn't when the server gave no reason", async () => {
    the.post.mockRejectedValue(new ApiError(503, { detail: "Busy" }));
    const { r, reread } = await changing();
    await act(async () => r.result.mark(3, false));
    await reread();
    expect(the.toast).toHaveBeenCalledWith("Couldn’t change that task.");
  });
});
