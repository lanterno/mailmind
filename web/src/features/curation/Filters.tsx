// What a person asks of mail as it arrives: a search, and whether to star what fits it or mark it read. The list sits with the learned
// rules; a search offers to make one. The server says what a filter can't look at, in words the dialog shows.
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Dialog, Modal, ModalOverlay } from "react-aria-components";
import { get, post, refusalOf } from "../../lib/api/client";
import { useLayer } from "../../lib/backTrap";
import { toast } from "../../lib/toast";
import { filterForm, newFilter, type FilterForm } from "./open";

const does = (f: { star: boolean; read: boolean }) => (f.star && f.read ? "Star and mark read" : f.star ? "Star" : "Mark read");

function Fields({ form }: { form: FilterForm }) {
  const qc = useQueryClient();
  const [q, setQ] = useState(form.q);
  const [star, setStar] = useState(form.star);
  const [read, setRead] = useState(form.read);
  const [why, setWhy] = useState("");
  const [saving, setSaving] = useState(false);
  const save = async () => {
    setSaving(true);
    setWhy("");
    try {
      await post("/api/filters", { verb: form.id ? "update" : "create", id: form.id, q, star, read });
      void qc.invalidateQueries({ queryKey: ["rules"] });
      toast(form.id ? "Filter saved" : "Filter created");
      filterForm.set(null);
    } catch (e) {
      setWhy(refusalOf(e)?.detail || "Couldn’t save the filter.");
      setSaving(false);
    }
  };
  return (
    <form className="do-plan" onSubmit={(e) => { e.preventDefault(); void save(); }}>
      <div className="do-hd">{form.id ? "Edit filter" : "New filter"}</div>
      <label className="flt-lbl" htmlFor="filterQuery">New mail that fits this search</label>
      <input className="flt-in" id="filterQuery" spellCheck={false} placeholder="from:name@host subject:invoice" value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="flt-does">
        <label><input type="checkbox" id="filterStar" checked={star} onChange={(e) => setStar(e.target.checked)} /> Star it</label>
        <label><input type="checkbox" id="filterRead" checked={read} onChange={(e) => setRead(e.target.checked)} /> Mark it read</label>
      </div>
      <div className="flt-note">Applies to new mail arriving in your Inbox, not to what’s already here. Your mail server hears of it too. A filter never moves mail.</div>
      {why && <div className="do-err" id="filterWhy" role="alert">{why}</div>}
      <div className="do-foot">
        <button type="submit" className="do-confirm" id="filterSave" disabled={saving}>{form.id ? "Save" : "Create filter"}</button>
        <button type="button" className="do-cancel" onClick={() => filterForm.set(null)}>Cancel</button>
      </div>
    </form>
  );
}

export function FilterDialog() {
  const form = filterForm.useValue();
  useLayer(form !== null, () => filterForm.set(null));
  return (
    <ModalOverlay className="do-overlay show" isOpen={form !== null} isDismissable onOpenChange={(open) => { if (!open) filterForm.set(null); }}>
      <Modal className="modal-through">
        <Dialog aria-label={form?.id ? "Edit filter" : "New filter"} className="do-panel">
          {form && <Fields form={form} />}
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}

export function Filters() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["rules"], queryFn: () => get("/api/rules") });
  const filters = data?.rules?.filters ?? [];
  const remove = async (id: string) => {
    try {
      await post("/api/filters", { verb: "delete", id });
      toast("Filter removed");
      void qc.invalidateQueries({ queryKey: ["rules"] });
    } catch {
      toast("Couldn’t remove the filter");
    }
  };
  return (
    <>
      <div id="filterList">
        {filters.length
          ? filters.map((f) => (
            <div className="flt" key={f.id}>
              <div className="flt-q"><code>{f.q}</code><span>{does(f)}</span></div>
              <button aria-label={`Edit the filter ${f.q}`} onClick={() => filterForm.set(f)}>Edit</button>
              <button aria-label={`Remove the filter ${f.q}`} onClick={() => void remove(f.id)}>Remove</button>
            </div>
          ))
          : <span style={{ color: "var(--faint)", fontSize: 10 }}>none</span>}
      </div>
      <button className="ctx-save" id="newFilter" onClick={() => filterForm.set(newFilter())}>+ New filter</button>
    </>
  );
}
