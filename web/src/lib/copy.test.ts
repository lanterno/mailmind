import { describe, expect, it } from "vitest";
import { copyFromId, copyId, copyQuery, eachCopyOnce, sameCopy } from "./copy";

describe("a copy's id", () => {
  it.each([
    { key: 7, folder: "INBOX", uid: 3 },
    { key: 7, folder: "Lists:Yarn & Wool", uid: 3 },
    { key: 12, folder: "a:1:b", uid: 4000000000 },
  ])("gives back the copy it was made of: %o", (c) => expect(copyFromId(copyId(c))).toEqual(c));
  it("differs for two copies of one key in one folder", () => {
    expect(copyId({ key: 7, folder: "INBOX", uid: 3 })).not.toBe(copyId({ key: 7, folder: "INBOX", uid: 4 }));
  });
});

describe("the same copy", () => {
  it("is the same key, folder and uid", () => {
    expect(sameCopy({ key: 7, folder: "INBOX", uid: 3 }, { key: 7, folder: "INBOX", uid: 3 })).toBe(true);
    expect(sameCopy({ key: 7, folder: "INBOX", uid: 3 }, { key: 7, folder: "INBOX", uid: 4 })).toBe(false);
    expect(sameCopy({ key: 7, folder: "INBOX", uid: 3 }, { key: 7, folder: "Archive", uid: 3 })).toBe(false);
    expect(sameCopy({ key: 7, folder: "INBOX", uid: 3 }, null)).toBe(false);
  });
});

describe("a copy in a query", () => {
  it("names its key, folder and uid", () => {
    expect(copyQuery({ key: 7, folder: "Lists/Yarn & Wool", uid: 3 })).toBe("key=7&folder=Lists%2FYarn%20%26%20Wool&uid=3");
  });
});

describe("each copy once", () => {
  it("keeps the first row of a copy, and both of two copies beside each other", () => {
    const rows = [{ key: 7, folder: "INBOX", uid: 3, n: 1 }, { key: 7, folder: "INBOX", uid: 4, n: 2 }, { key: 7, folder: "INBOX", uid: 3, n: 3 }];
    expect(eachCopyOnce(rows).map((r) => r.n)).toEqual([1, 2]);
  });
});
