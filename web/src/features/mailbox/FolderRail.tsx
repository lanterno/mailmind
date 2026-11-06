// The rail: which account, then its folders and the views that aren't folders. Each row is a link, so it opens in a new
// tab and Back works.
import { Button, Menu, MenuItem, MenuTrigger, Popover } from "react-aria-components";
import { go, pathOf, type ListSource } from "../../app/route";
import { shortAddress } from "../../lib/text";
import { Icon } from "../../lib/ui/Icon";
import { useFolders } from "./data";
import { FOLDER_ICON, roleLabel, type FolderInfo } from "./rows";

const ALL = "__all__";

const acctColor = (accounts: string[], a: string) => `var(--acct-${(Math.max(0, accounts.indexOf(a)) % 6) + 1})`;

function Dots({ accounts, of }: { accounts: string[]; of: string[] }) {
  return (
    <span className="acct-stack" aria-hidden="true">
      {of.slice(0, 4).map((a) => <i key={a} style={{ "--a": acctColor(accounts, a) } as React.CSSProperties} />)}
    </span>
  );
}

function AccountSwitch({ accounts, aliases, current }: { accounts: string[]; aliases: Record<string, string | null>; current: string }) {
  const nameOf = (a: string) => aliases[a] || shortAddress(a);
  const name = current === ALL ? "All inboxes" : nameOf(current);
  const sub = (a: string) => (a === ALL ? `${accounts.length} account${accounts.length === 1 ? "" : "s"}, one inbox` : a);
  const choose = (a: string) => go({ source: a === ALL ? { kind: "allInboxes" } : { kind: "folder", account: a, folder: "INBOX" }, opened: null });
  return (
    <MenuTrigger>
      <Button className="acct-sw" aria-label={`Account: ${name}. Switch account`}>
        <Dots accounts={accounts} of={current === ALL ? accounts : [current]} />
        <span className="as-txt"><b>{name}</b><small>{sub(current)}</small></span>
      </Button>
      <Popover className="acct-menu rac" placement="bottom start" offset={6}>
        <Menu selectionMode="single" selectedKeys={[current]} autoFocus="first" onAction={(key) => choose(String(key))}>
          {[ALL, ...accounts].map((a) => (
            <MenuItem key={a} id={a} textValue={a === ALL ? "All inboxes" : nameOf(a)} className="am-item">
              <Dots accounts={accounts} of={a === ALL ? accounts : [a]} />
              <span className="am-txt"><b>{a === ALL ? "All inboxes" : nameOf(a)}</b><small>{sub(a)}</small></span>
            </MenuItem>
          ))}
        </Menu>
      </Popover>
    </MenuTrigger>
  );
}

function RailRow({ source, on, icon, label, count }: { source: ListSource; on: boolean; icon: string; label: string; count?: number | undefined }) {
  return (
    <a
      className={on ? "frow on" : "frow"}
      href={pathOf({ source, opened: null })}
      data-folder={source.kind === "folder" ? source.folder : undefined}
      aria-current={on ? "page" : undefined}
      onClick={(e) => {
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
        e.preventDefault();
        go({ source, opened: null });
      }}
    >
      <span className="ic2"><Icon name={icon} /></span><span className="fn">{label}</span>
      {count ? <span className="unread">{count}</span> : null}
    </a>
  );
}

export function FolderRail({ accounts, aliases, source }: { accounts: string[]; aliases: Record<string, string | null>; source: ListSource | null }) {
  const account = source?.kind === "folder" ? source.account : source?.kind === "search" && source.account ? source.account : source?.kind === "allInboxes" ? undefined : accounts[0];
  const { data, isError, refetch } = useFolders(account);
  const folder = source?.kind === "folder" ? source.folder : "";
  const to = (name: string): ListSource => ({ kind: "folder", account: account!, folder: name });
  const current = source?.kind === "allInboxes" ? ALL : account ?? ALL;
  const views: [string, string, string, number | undefined][] = data ? [
    ["__replylater__", "bookmark", "Reply later", data.replylater],
    ["__waiting__", "hourglass", "Waiting", data.waiting],
    ["__muted__", "bell-off", "Muted", data.muted],
  ] : [];
  return (
    <aside className="folderrail" id="rail" aria-label="Accounts and folders">
      <div id="monos">{accounts.length > 0 && <AccountSwitch accounts={accounts} aliases={aliases} current={current} />}</div>
      <nav className="frail-list" id="folderrail" aria-label="Folders">
        {source?.kind === "allInboxes" && <div className="frail-acct">{accounts.length} inboxes, newest first</div>}
        {isError && !data && (
          <>
            <div className="frail-acct">Couldn’t list folders</div>
            <button className="frail-retry" onClick={() => void refetch()}>↻ Retry</button>
          </>
        )}
        {data?.folders.map((f: FolderInfo) => (
          <RailRow key={f.name} source={to(f.name)} on={f.name === folder} icon={FOLDER_ICON[f.role] ?? "folder"} label={roleLabel(f)} count={f.unseen} />
        ))}
        {data && <RailRow source={to("__snoozed__")} on={folder === "__snoozed__"} icon="clock" label="Snoozed" count={data.snoozed} />}
        {views.map(([name, icon, label, n]) => (n ? <RailRow key={name} source={to(name)} on={folder === name} icon={icon} label={label} count={n} /> : null))}
      </nav>
    </aside>
  );
}
