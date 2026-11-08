// The brief's reads. A GET reads only what is stored; a POST writes one. Opening the Inbox writes the day's brief if there
// is none, and never asks at the daily limit: only a click does.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, get, post } from "../../lib/api/client";
import type { GetAnswers } from "../../lib/api/types";
import { askOverLimit } from "../../lib/overLimit";
import type { Brief } from "./model";

// A review written: a read that finds none stored writes one.
export type Review = Exclude<GetAnswers["/api/review"], { missing: true }>;

export const LIMIT = "limit";
// What went wrong, for the words beside "Try again". AI off isn't a failure: the brief says so.
export function whatWentWrong(e: unknown): string | null {
  if (!e) return null;
  if (e instanceof ApiError) return e.overLimit ? LIMIT : e.aiOff ? null : "couldn’t reach the server";
  return e instanceof Error && e.message ? e.message : "couldn’t reach the server";
}

const held = <T extends { ok?: boolean; error?: string }>(d: T): T => {
  if (!d.ok) throw new Error(d.error || "no answer");
  return d;
};

export function useBrief(ai: boolean | undefined) {
  const qc = useQueryClient();
  const read = useQuery({
    queryKey: ["brief"],
    enabled: ai !== undefined,
    staleTime: Infinity,
    retry: false,
    queryFn: async (): Promise<Brief> => {
      const d = await get("/api/briefing");
      return "missing" in d && ai ? held(await post("/api/briefing", {})) as Brief : held(d);
    },
  });
  const write = useMutation({
    mutationFn: async (again: boolean) => held(await post(again ? "/api/briefing/refresh" : "/api/briefing", {}, { askOverLimit })) as Brief,
    onSuccess: (d) => qc.setQueryData(["brief"], d),
    onSettled: (_d, _e, again) => {
      if (again) void qc.invalidateQueries({ queryKey: ["briefSide"] });
    },
  });
  return {
    brief: read.data,
    writing: read.isFetching || write.isPending,
    error: whatWentWrong(write.error ?? read.error),
    write: (again: boolean) => write.mutate(again),
  };
}

export function useSide() {
  return useQuery({
    queryKey: ["briefSide"],
    staleTime: 60_000,
    queryFn: async () => {
      const [up, waiting] = await Promise.all([
        get("/api/upcoming").catch(() => null),
        get("/api/waiting-on-others").catch(() => null),
      ]);
      return { upcoming: up?.items ?? [], waiting: waiting?.items ?? [] };
    },
  });
}

export function useReview(open: boolean) {
  const qc = useQueryClient();
  const read = useQuery({
    queryKey: ["review"],
    enabled: open,
    staleTime: Infinity,
    retry: false,
    queryFn: async (): Promise<Review> => {
      const d = await get("/api/review");
      return "missing" in d ? await post("/api/review", {}, { askOverLimit }) as Review : d;   // none yet: you opened it, so write it
    },
  });
  const again = useMutation({
    mutationFn: async () => await post("/api/review/refresh", {}, { askOverLimit }) as Review,
    onSuccess: (d) => qc.setQueryData(["review"], d),
  });
  const error = again.error ?? read.error;
  return {
    review: again.isPending ? undefined : read.data,
    loading: again.isPending || (read.isFetching && !read.data),
    error,
    again: () => again.mutate(),
  };
}
