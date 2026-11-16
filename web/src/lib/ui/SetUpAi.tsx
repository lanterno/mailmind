import { openSettings } from "../settingsOpen";

// Where AI is off for want of a key, a model or a URL: the way to Settings, at AI. `first` closes what the button stands in.
export function SetUpAi({ first, className = "btn-retry" }: { first?: () => void; className?: string }) {
  return (
    <button type="button" className={className} onClick={() => { first?.(); openSettings("ai"); }}>Set up AI in Settings</button>
  );
}
