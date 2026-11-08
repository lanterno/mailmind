// Whether what's typed in the search box reads as a sentence, worth asking the model to turn into a search. It decides a call
// that costs money, on Enter and only then, so it leans hard towards no: a miss is only a literal search, which is what the box
// has always done, and `!` still asks. Three or more words that don't speak the grammar, opening like a request or a question, or
// holding a word that relates one term to another, or a time said in words.
//
// The words are chosen so that names and titles don't pass: "bank of america", "the new york times", "thanks for lunch" and
// "meeting with john" hold only words that join a phrase, not words that ask. English only, as the model's prompt is: anything
// else stays literal.

const FEWEST = 3;
const MOST = 30;       // more than this was pasted, not typed as a search

const ASKS = new Set(["where", "what", "when", "who", "whom", "which", "why", "how", "did", "does", "has", "is", "are", "was", "were"]);
const REQUESTS = new Set(["find", "show", "get", "search", "look", "list"]);     // only with REQUESTED after: "list of attendees" isn't one
const REQUESTED = new Set(["me", "my", "the", "all", "any", "every", "emails", "mail", "messages", "for"]);
const RELATES = new Set(["from", "about", "since", "before", "after", "regarding", "between", "mentioning", "containing", "sent", "received", "wrote", "attached"]);
const TIME_ALONE = new Set(["yesterday", "today", "tomorrow", "ago", "recently", "lately"]);
const NEAR = new Set(["last", "this", "next", "past"]);
const SPAN = new Set(["week", "month", "year", "night", "weekend", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]);

export function readsAsProse(typed: string): boolean {
  const text = typed.trim();
  if (text.includes('"')) return false;                              // a phrase in quotes means exactly that
  const raw = text.split(/\s+/);
  if (raw.length < FEWEST || raw.length > MOST) return false;
  if (raw.some((w) => /^-./.test(w) || /^[\p{L}\p{N}_]+:\S/u.test(w))) return false;   // a negation or an operator: the grammar, meant
  const words = raw.map((w) => w.toLowerCase().replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, ""));
  if (ASKS.has(words[0]!) || (REQUESTS.has(words[0]!) && REQUESTED.has(words[1]!))) return true;
  return words.some((w, i) =>
    (RELATES.has(w) && i > 0 && i < words.length - 1)
    || TIME_ALONE.has(w)
    || (NEAR.has(w) && SPAN.has(words[i + 1] ?? "")));
}
