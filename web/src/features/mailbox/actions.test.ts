import { expect, it, vi } from "vitest";

const aPageLoad = async () => {
  vi.resetModules();
  return (await import("./actions")).undoGroup;
};

it("gives each undo group an id of its own across page loads, since the undo stack outlives a page", async () => {
  const first = await aPageLoad(), second = await aPageLoad();
  const ids = [first(), first(), second(), second()];
  expect(new Set(ids).size).toBe(4);
  for (const id of ids) expect(id).toMatch(/^ug-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
});
