import { describe, expect, it } from "vitest";
import { readsAsProse } from "./prose";

describe("what reads as a sentence", () => {
  it.each([
    "invoices from last month",
    "emails from sarah about the budget",
    "show me unread mail from lin",
    "find the invoice from acme",
    "what did stripe send me",
    "where is my boarding pass?",
    "receipts since october",
    "mail about the quilt",
    "anything from lin yesterday",
    "flights last week",
    "the invoice lin sent me",
    "Show Me Unread Mail",
    "look for invoices from acme",
  ])("%s", (typed) => expect(readsAsProse(typed)).toBe(true));
});

describe("what stays a literal search", () => {
  it.each([
    ["one word", "budget"],
    ["two words", "from lin"],
    ["keywords", "quarterly budget review"],
    ["more keywords", "quarterly budget review meeting"],
    ["numbers", "invoice 12345 acme"],
    ["a name with of", "bank of america"],
    ["a title", "lord of the rings"],
    ["a name with the", "the new york times"],
    ["a thanks", "thanks for lunch"],
    ["a meeting", "meeting with john"],
    ["no reply", "do not reply"],
    ["a noun that could be a verb", "list of attendees"],
    ["a greeting", "get well soon"],
    ["another", "have a nice day"],
    ["no article after the verb", "find invoice acme"],
    ["a word that only ends a phrase", "annual report from"],
    ["a word that only starts one", "from the desk"],
    ["an operator", "from:lin last month"],
    ["an operator at the end", "mail from lin is:unread"],
    ["a negation", "mail from lin -newsletter"],
    ["quotes", '"a phrase from lin" budget'],
    ["a time word that isn't one", "this year's budget"],
    ["another language", "Rechnungen vom letzten Monat"],
    ["nothing", "   "],
    ["pasted text", Array(31).fill("mail from lin").join(" ")],
  ])("%s", (_, typed) => expect(readsAsProse(typed)).toBe(false));
});
