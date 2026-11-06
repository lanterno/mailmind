// ⋯ More: the verbs this width's container tier hid, in bar order. Picking one clicks the hidden button itself, so each
// verb keeps a single code path.
import { useState } from "react";
import { Button, Menu, MenuItem, MenuTrigger, Popover } from "react-aria-components";
import { Icon } from "../../lib/ui/Icon";

interface Hidden { id: string; name: string; button: HTMLElement }

function hiddenVerbs(from: Element | null): Hidden[] {
  if (!from) return [];
  const verbs = from.querySelectorAll<HTMLElement>(".cab[data-ca],.cab[data-tca],.cab[data-readset]");
  return [...verbs].filter((b) => !b.getClientRects().length).map((button, i) => ({
    id: String(i),
    name: (button.getAttribute("aria-label") ?? button.textContent ?? "").replace(/\s+/g, " ").trim(),
    button,
  }));
}

export function MoreMenu() {
  const [hidden, setHidden] = useState<Hidden[]>([]);
  return (
    <MenuTrigger onOpenChange={(open) => { if (open) setHidden(hiddenVerbs(document.querySelector("#reader .rd-actions"))); }}>
      <Button className="cab rd-more" aria-label="More actions"><span><Icon name="more" /></span></Button>
      <Popover className="snooze-menu rac" placement="bottom end" offset={4}>
        <Menu items={hidden} onAction={(key) => hidden.find((h) => h.id === key)?.button.click()} renderEmptyState={() => <div className="nores">Nothing else here.</div>}>
          {(h) => <MenuItem className="am-item" textValue={h.name}>{h.name}</MenuItem>}
        </Menu>
      </Popover>
    </MenuTrigger>
  );
}
