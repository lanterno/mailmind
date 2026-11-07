// The rail: which account, then its folders and the views that aren't folders. Each row is a link, so it opens in a new
// tab and Back works.
import { useEffect, useRef } from "react";
import { Button, Menu, MenuItem, MenuTrigger, Popover, Separator } from "react-aria-components";
import { go, pathOf, type ListSource } from "../../app/route";
import { shortAddress } from "../../lib/text";
import { Icon } from "../../lib/ui/Icon";
import { post } from "../../lib/api/client";
import { toast } from "../../lib/toast";
import { useQueryClient } from "@tanstack/react-query";
import { useScreener } from "../curation";
import { settingsOpen } from "../settings";
import { useFolders, useViews } from "./data";
import { FOLDER_ICON, roleLabel, type FolderInfo } from "./rows";

const ALL = "__all__", MANAGE = "__manage__";

const acctColor = (accounts: string[], a: string) => `var(--acct-${(Math.max(0, accounts.indexOf(a)) % 6) + 1})`;

function Dots({ accounts, of }: { accounts: string[]; of: string[] }) {
  return (
    <span className="acct-stack" aria-hidden="true">
      {of.slice(0, 4).map((a) => <i key={a} style={{ "--a": acctColor(accounts, a) } as React.CSSProperties} />)}
    </span>
  );
}

function AccountSwitch({ accounts, aliases, current, down }: { accounts: string[]; aliases: Record<string, string | null>; current: string; down: string[] }) {
  const nameOf = (a: string) => aliases[a] || shortAddress(a);
  const name = current === ALL ? "All inboxes" : nameOf(current);
  const isDown = (a: string) => down.includes(a);
  const sub = (a: string) => (a === ALL ? `${accounts.length} account${accounts.length === 1 ? "" : "s"}, one inbox` : isDown(a) ? "can’t connect" : a);
  const head = current === ALL && down.length ? `${down.length} of ${accounts.length} can’t connect` : sub(current);
  const button = useRef<HTMLButtonElement>(null);
  useEffect(() => button.current?.setAttribute("title", `${name} — switch account`), [name]);   // React Aria's Button drops a title
  const choose = (a: string) => {
    if (a === MANAGE) settingsOpen.set(true);
    else go({ source: a === ALL ? { kind: "allInboxes" } : { kind: "folder", account: a, folder: "INBOX" }, opened: null });
  };
  return (
    <MenuTrigger>
      <Button className="acct-sw" id="acctSw" ref={button} aria-label={`Account: ${name}${down.length ? ` (${head})` : ""}. Switch account`}>
        <Dots accounts={accounts} of={current === ALL ? accounts : [current]} />
        <span className="as-txt"><b>{name}</b><small className={down.length && (current === ALL || isDown(current)) ? "err" : undefined}>{head}</small></span>
        <Icon name="down" className="as-chev" />
      </Button>
      <Popover className="acct-menu rac" placement="bottom start" offset={6}>
        <div className="am-ttl" aria-hidden="true">Show mail from</div>
        <Menu selectionMode="single" selectedKeys={[current]} autoFocus onAction={(key) => choose(String(key))}>
          {[ALL, ...accounts].map((a) => (
            <MenuItem key={a} id={a} textValue={a === ALL ? "All inboxes" : nameOf(a)} className="am-item">
              <Dots accounts={accounts} of={a === ALL ? accounts : [a]} />
              <span className="am-txt"><b>{a === ALL ? "All inboxes" : nameOf(a)}</b><small className={a !== ALL && isDown(a) ? "err" : undefined}>{sub(a)}</small></span>
              {a === current && <Icon name="check" className="am-chk" />}
            </MenuItem>
          ))}
          <Separator />
          <MenuItem id={MANAGE} textValue="Add or manage accounts" className="am-item">
            <Icon name="sliders" /><span className="am-txt"><b>Add or manage accounts</b></span>
          </MenuItem>
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

// A view that changes with the day says so in its query.
export function viewQuery(v: { q: string; dynamic?: string }, now = new Date()): string {
  if (v.dynamic !== "last7" && v.q !== "__LAST7__") return v.q;
  return `after:${new Date(now.getTime() - 7 * 864e5).toISOString().slice(0, 10)}`;
}

function Views({ source, account }: { source: ListSource | null; account: string }) {
  const qc = useQueryClient();
  const { data } = useViews();
  const views = data?.views ?? [];
  if (!views.length) return null;
  const remove = async (id: string) => {
    try {
      await post("/api/views", { verb: "delete", id });
      void qc.invalidateQueries({ queryKey: ["views"] });
      toast("View removed");
    } catch {
      toast("Couldn’t remove the view");
    }
  };
  return (
    <>
      <div className="frail-acct">Views</div>
      {views.map((v) => {
        const q = viewQuery(v);
        const lane = v.id === "__lane_feed__" ? "feed" : v.id === "__lane_papertrail__" ? "papertrail" : undefined;
        const to: ListSource = { kind: "search", q, account, ...(lane ? { lane } : {}) };
        const on = source?.kind === "search" && source.q === q && source.lane === lane;
        return (
          <a
            key={v.id}
            className={on ? "frow on" : "frow"}
            data-view={v.id}
            href={pathOf({ source: to, opened: null })}
            aria-current={on ? "page" : undefined}
            onClick={(e) => {
              if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
              e.preventDefault();
              go({ source: to, opened: null });
            }}
          >
            <span className="ic2"><Icon name={lane === "feed" ? "book" : lane === "papertrail" ? "receipt" : "search"} /></span><span className="fn">{v.name}</span>
            {!v.builtin && <span className="vdel" aria-hidden="true" title="Remove view" onClick={(e) => { e.preventDefault(); e.stopPropagation(); void remove(v.id); }}>×</span>}
          </a>
        );
      })}
    </>
  );
}

export function FolderRail({ accounts, aliases, source, down }: { accounts: string[]; aliases: Record<string, string | null>; source: ListSource | null; down: string[] }) {
  const account = source?.kind === "folder" ? source.account : source?.kind === "search" && source.account ? source.account : source?.kind === "allInboxes" ? undefined : accounts[0];
  const { data, isError, refetch } = useFolders(account);
  const screener = useScreener(account).data?.total ?? 0;
  const folder = source?.kind === "folder" ? source.folder : "";
  const to = (name: string): ListSource => ({ kind: "folder", account: account!, folder: name });
  const current = source?.kind === "allInboxes" ? ALL : account ?? ALL;
  const virtual: [string, string, string, number | undefined][] = data ? [
    ["__replylater__", "bookmark", "Reply later", data.replylater],
    ["__waiting__", "hourglass", "Waiting", data.waiting],
    ["__snoozed__", "clock", "Snoozed", data.snoozed],
    ["__muted__", "bell-off", "Muted", data.muted],
  ] : [];
  return (
    <aside className="folderrail" id="rail" aria-label="Accounts and folders">
      <div id="monos">{accounts.length > 0 && <AccountSwitch accounts={accounts} aliases={aliases} current={current} down={down} />}</div>
      <nav className="frail-list" id="folderrail" aria-label="Folders">
        {source?.kind === "allInboxes" && <div className="frow on"><span className="ic2"><Icon name="inbox" /></span><span className="fn">Inbox</span></div>}
        {isError && !data && (
          <>
            <div className="frail-acct">Couldn’t list folders</div>
            <button className="frail-retry" onClick={() => void refetch()}>↻ Retry</button>
          </>
        )}
        {data?.folders.map((f: FolderInfo) => (
          <RailRow key={f.name} source={to(f.name)} on={f.name === folder} icon={FOLDER_ICON[f.role] ?? "folder"} label={roleLabel(f)} count={f.unseen} />
        ))}
        <Views source={source} account={account ?? ""} />
        {screener > 0 && <RailRow source={to("__screener__")} on={folder === "__screener__"} icon="gate" label="Screener" count={screener} />}
        {virtual.map(([name, icon, label, n]) => (n || name === "__snoozed__" ? <RailRow key={name} source={to(name)} on={folder === name} icon={icon} label={label} count={n} /> : null))}
      </nav>
    </aside>
  );
}
