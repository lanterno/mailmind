// A folder on its first read, and how far that has got. Its mail is stored batch by batch, so what is stored grows as it goes: this
// counts that growth since the line came up, from /api/digest, which holds one total for all the mail, so another account's sync in
// the same moment adds to it. The count stays out of the status line, or a screen reader would say it again every few seconds.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { get } from "../../lib/api/client";

interface Progress { from: number; total: number }

const KEY = ["firstRead"];

export function FirstRead() {
  const client = useQueryClient();
  const { data } = useQuery({
    queryKey: KEY,
    queryFn: async (): Promise<Progress> => {
      const total = (await get("/api/digest")).stats?.total ?? 0;
      return { from: client.getQueryData<Progress>(KEY)?.from ?? total, total };
    },
    refetchInterval: 3000,
    gcTime: 0,                 // the next folder counts from its own start
  });
  const read = data ? data.total - data.from : 0;
  return (
    <div className="nores">
      <div role="status">Reading this folder for the first time…</div>
      {read > 0 && <div className="nores-sub" id="firstReadCount">Read {read.toLocaleString()} message{read === 1 ? "" : "s"} so far…</div>}
    </div>
  );
}
