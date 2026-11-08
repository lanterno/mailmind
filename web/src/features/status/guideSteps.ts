// The first-run guide's words, when it is offered and where its coach-marks sit. No DOM.

export interface GuideStep {
  id: string;
  at: string[];        // what it points at: the first of these on screen, or nothing where none is
  title: string;
  text: string;
}

// Hold it to what is true on this install: a key that's switched off isn't taught, and nor is what needs an AI that isn't on.
export function guideSteps({ ai, singleKeys }: { ai: boolean; singleKeys: boolean }): GuideStep[] {
  return [
    { id: "brief", at: [".reader.brief-pane"], title: "Today’s brief", text: "Your Inbox opens on this page: what needs you, what’s safe to file, and what’s coming up." },
    {
      id: "rail", at: ["#rail"], title: "Folders, and focus reading",
      text: singleKeys
        ? "Your accounts and folders are here. Open a message and press ↵, or Focus reading above it, to hide all this and just read."
        : "Your accounts and folders are here. Open a message and use Focus reading above it to hide all this and just read.",
    },
    { id: "palette", at: [], title: "⌘K opens every command", text: "Folders, filing, settings and more, by name. Press ⌘K (Ctrl K on Windows and Linux) and start typing." },
    ...(ai
      ? [{ id: "ask", at: ["#search"], title: "Ask, or tell it what to do", text: "Start the search box with ? to ask your mail a question, or with ! to say what you want done. A ! shows you what it would do, and nothing happens until you confirm." }]
      : []),
    { id: "screener", at: ['a.frow[data-folder="__screener__"]', "#rail"], title: "The Screener", text: "Someone new has written? They wait in the Screener for you to decide. Allow keeps them, and Screen out stops their mail reaching your Inbox." },
  ];
}

export interface GuideGate {
  prefsRead: boolean;      // the server has answered, so `offered` is what it holds and not this browser's guess
  offered: boolean;
  accounts: number;
  inboxHasMail: boolean;   // an Inbox is on screen, with mail in it and nothing opened: what the first step points at
  busy: boolean;           // a dialog or the composer is up, and this would land on top of it
}

// Offered once, after the first account has mail and with nothing else in the way.
export const guideOffers = (g: GuideGate) => g.prefsRead && !g.offered && g.accounts > 0 && g.inboxHasMail && !g.busy;

export interface Box { top: number; left: number; width: number; height: number }
export interface Size { width: number; height: number }

const GAP = 12;

// Where a coach-mark sits: beside what it points at, else under or over it, inside the window whichever. With nothing to point at, centred.
export function coachPlace(target: Box | null, view: Size, card: Size): { top: number; left: number } {
  const across = (x: number) => Math.max(GAP, Math.min(x, view.width - card.width - GAP));
  const down = (y: number) => Math.max(GAP, Math.min(y, view.height - card.height - GAP));
  if (!target) return { top: down((view.height - card.height) / 2), left: across((view.width - card.width) / 2) };
  const right = target.left + target.width + GAP;
  if (right + card.width <= view.width - GAP) return { top: down(target.top), left: right };
  const left = target.left - GAP - card.width;
  if (left >= GAP) return { top: down(target.top), left };
  const under = target.top + target.height + GAP;
  if (under + card.height <= view.height - GAP) return { top: under, left: across(target.left) };
  return { top: down(target.top - GAP - card.height), left: across(target.left) };
}
