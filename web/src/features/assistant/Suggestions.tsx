// What the open message proposes, found by Claude on a click. Each is a suggestion: its fields can be changed, and nothing is
// made until someone confirms it. An event becomes a file for their calendar and invites no one; a task joins their list.
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, post, refusalOf } from "../../lib/api/client";
import type { PostAnswers } from "../../lib/api/types";
import { copyId, copyOf, type Copy } from "../../lib/copy";
import { askOverLimit } from "../../lib/overLimit";
import { createStore } from "../../lib/store";
import { toast } from "../../lib/toast";
import { Icon } from "../../lib/ui/Icon";
import { fromField, toField } from "./fields";

type Found = PostAnswers["/api/ai/extract"];
type Suggested = Found["events"][number];
type SuggestedTask = Found["tasks"][number];
type Card = { kind: "loading" } | { kind: "error"; error: string } | { kind: "done"; found: Found };

const cards = createStore<{ id: string; card: Card } | null>(null);
const inFlight = new Set<string>();

// Asks once however many times it's clicked, and shows what it found in the reader.
export async function findSuggestions(copy: Copy) {
  const id = copyId(copy);
  if (inFlight.has(id)) return;
  inFlight.add(id);
  const show = (card: Card) => cards.set({ id, card });
  show({ kind: "loading" });
  try {
    show({ kind: "done", found: await post("/api/ai/extract", copyOf(copy), { askOverLimit }) });
  } catch (e) {
    const off = e instanceof ApiError && e.aiOff;
    show({ kind: "error", error: off ? "AI is off, so nothing can read this for dates." : refusalOf(e)?.detail || "Couldn’t read this message for dates." });
  } finally {
    inFlight.delete(id);
  }
}

function saveFile(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/calendar" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="sg-field"><span>{label}</span>{children}</label>;
}

function EventForm({ copy, event, drop }: { copy: Copy; event: Suggested; drop: () => void }) {
  const [title, setTitle] = useState(event.title);
  const [start, setStart] = useState(toField(event.start, event.all_day));
  const [end, setEnd] = useState(toField(event.end, event.all_day));
  const [place, setPlace] = useState(event.place);
  const [refused, setRefused] = useState("");
  const kind = event.all_day ? "date" : "datetime-local";
  const confirm = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const d = await post("/api/calendar/event", { ...copyOf(copy), title, start: fromField(start, event.all_day), end: fromField(end, event.all_day), place });
      saveFile(d.filename, d.ics);
      toast("Saved — open the file to add it to your calendar");
      drop();
    } catch (err) {
      setRefused(refusalOf(err)?.detail || "Couldn’t make the file.");
    }
  };
  return (
    <form className="sg-row" data-suggestion="event" onSubmit={(e) => void confirm(e)}>
      <div className="sg-kind"><Icon name="cal" /> Event</div>
      <div className="sg-fields">
        <Field label="Title"><input className="sg-title" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} /></Field>
        <Field label="Starts"><input type={kind} value={start} onChange={(e) => setStart(e.target.value)} /></Field>
        <Field label="Ends"><input type={kind} value={end} onChange={(e) => setEnd(e.target.value)} /></Field>
        <Field label="Place"><input value={place} maxLength={300} onChange={(e) => setPlace(e.target.value)} /></Field>
      </div>
      {refused && <div className="sg-refused" role="alert">{refused}</div>}
      <div className="sg-acts">
        <button type="submit" className="cab">Add to calendar</button>
        <button type="button" className="btn-retry" onClick={drop}>Dismiss</button>
      </div>
    </form>
  );
}

function TaskForm({ copy, task, drop }: { copy: Copy; task: SuggestedTask; drop: () => void }) {
  const qc = useQueryClient();
  const [title, setTitle] = useState(task.title);
  const [due, setDue] = useState(task.due ?? "");
  const [refused, setRefused] = useState("");
  const confirm = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await post("/api/tasks", { ...copyOf(copy), title, due });
      void qc.invalidateQueries({ queryKey: ["tasks"] });
      toast("Added to your tasks");
      drop();
    } catch (err) {
      setRefused(refusalOf(err)?.detail || "Couldn’t add that task.");
    }
  };
  return (
    <form className="sg-row" data-suggestion="task" onSubmit={(e) => void confirm(e)}>
      <div className="sg-kind"><Icon name="todo" /> Task</div>
      <div className="sg-fields">
        <Field label="Title"><input className="sg-title" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} /></Field>
        <Field label="Due"><input type="date" value={due} onChange={(e) => setDue(e.target.value)} /></Field>
      </div>
      {refused && <div className="sg-refused" role="alert">{refused}</div>}
      <div className="sg-acts">
        <button type="submit" className="cab">Add to tasks</button>
        <button type="button" className="btn-retry" onClick={drop}>Dismiss</button>
      </div>
    </form>
  );
}

function Lists({ copy, found }: { copy: Copy; found: Found }) {
  // What is confirmed or dismissed leaves its list. The model's index names each, as the same words can come twice.
  const [gone, setGone] = useState<ReadonlySet<string>>(new Set());
  const drop = (id: string) => () => setGone((was) => new Set([...was, id]));
  const events = found.events.map((e, i) => ({ e, id: `e${i}` })).filter(({ id }) => !gone.has(id));
  const tasks = found.tasks.map((t, i) => ({ t, id: `t${i}` })).filter(({ id }) => !gone.has(id));
  if (!events.length && !tasks.length) {
    return <div className="ai-tldr" id="sgNone">{found.events.length + found.tasks.length ? "That’s everything it found." : "Nothing here proposes a meeting or a dated task."}</div>;
  }
  return (
    <>
      {events.map(({ e, id }) => <EventForm key={id} copy={copy} event={e} drop={drop(id)} />)}
      {tasks.map(({ t, id }) => <TaskForm key={id} copy={copy} task={t} drop={drop(id)} />)}
    </>
  );
}

export function SuggestionCard({ copy }: { copy: Copy | null }) {
  const state = cards.useValue();
  if (!copy) return null;
  const shown = state?.id === copyId(copy) ? state.card : null;
  if (!shown) return null;
  const heading = <span className="ai-mark"><Icon name="todo" />Found in this message</span>;
  if (shown.kind === "loading") {
    return (
      <div className="ai-card" id="sgCard">
        <div className="ai-hd">{heading}</div>
        <div className="rd-skel"><div className="skb" /><div className="skb short" /></div>
      </div>
    );
  }
  if (shown.kind === "error") {
    return <div className="ai-card" id="sgCard"><div className="ai-err">{shown.error} <button className="btn-retry" onClick={() => void findSuggestions(copy)}>Retry</button></div></div>;
  }
  return (
    <div className="ai-card" id="sgCard">
      <div className="ai-hd">{heading}</div>
      <Lists key={JSON.stringify(shown.found)} copy={copy} found={shown.found} />
      <div className="ai-foot">Nothing is added until you confirm it · {shown.found.model}</div>
    </div>
  );
}
