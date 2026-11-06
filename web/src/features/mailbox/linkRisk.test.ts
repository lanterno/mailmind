import { describe, expect, it } from "vitest";
import { linkHosts, linkRisk } from "./linkRisk";

describe("linkRisk", () => {
  it("lets through a plain link and anything that isn't http", () => {
    expect(linkRisk("https://guild.example.test/events", "our events", "guild.example.test")).toBeNull();
    expect(linkRisk("mailto:bo@example.test", "write to Bo", undefined)).toBeNull();
    expect(linkRisk("not a url", "x", undefined)).toBeNull();
  });
  it("warns on punycode", () => {
    expect(linkRisk("https://xn--pypal-4ve.example.test/", "pay", undefined)).toMatch(/punycode/);
  });
  it("warns when the text names another domain", () => {
    expect(linkRisk("https://evil.example.test/", "bank.example.test", undefined)).toMatch(/different domain \(evil\.example\.test\).*bank\.example\.test/);
  });
  it("isn't fooled by the claimed name planted in a path, a query or a subdomain", () => {
    expect(linkRisk("https://evil.example.test/bank.example.org", "bank.example.org", undefined)).toMatch(/different domain/);
    expect(linkRisk("https://evil.example.test/?u=bank.example.org", "bank.example.org", undefined)).toMatch(/different domain/);
    expect(linkRisk("https://bank.example.org.evil.test/", "bank.example.org", undefined)).toMatch(/different domain/);
  });
  it("accepts a redirector that really goes to the name in the text", () => {
    expect(linkRisk("https://tracker.example.test/r?to=https%3A%2F%2Fshop.example.org%2Fa", "shop.example.org", undefined)).toBeNull();
    expect(linkRisk("https://mail.shop.example.org/a", "shop.example.org", undefined)).toBeNull();
  });
  it("warns on a sign-in link that leaves the sender's domain", () => {
    expect(linkRisk("https://other.example.org/x", "Verify your account", "bank.example.test")).toMatch(/unfamiliar domain/);
    expect(linkRisk("https://www.example.test/x", "Verify your account", "bank.example.test")).toBeNull();
    expect(linkRisk("https://other.example.org/x", "Read more", "bank.example.test")).toBeNull();
  });
});

it("finds the hosts of a link and of any URL inside it", () => {
  expect(new Set(linkHosts("https://www.a.example.test/r?u=https%3A%2F%2Fb.example.test"))).toEqual(new Set(["a.example.test", "b.example.test"]));
  expect(new Set(linkHosts("https://a.example.test/%zz"))).toEqual(new Set(["a.example.test"]));
});
