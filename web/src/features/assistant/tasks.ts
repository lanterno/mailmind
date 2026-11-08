// The tasks someone confirmed from their mail: a read, and what changes one.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post, refusalOf } from "../../lib/api/client";
import type { GetAnswers } from "../../lib/api/types";
import { toast } from "../../lib/toast";

export type Task = GetAnswers["/api/tasks"]["items"][number];

export function useTasks() {
  return useQuery({ queryKey: ["tasks"], queryFn: async () => (await get("/api/tasks")).items, staleTime: 60_000 });
}

export function useTaskChanges() {
  const qc = useQueryClient();
  const settled = () => void qc.invalidateQueries({ queryKey: ["tasks"] });
  const said = (e: unknown) => toast(refusalOf(e)?.detail || "Couldn’t change that task.");
  const mark = useMutation({
    mutationFn: async ({ id, done }: { id: number; done: boolean }) => await post("/api/tasks/done", { id, done }),
    onError: said,
    onSettled: settled,
  });
  const remove = useMutation({
    mutationFn: async (id: number) => await post("/api/tasks/remove", { id }),
    onError: said,
    onSettled: settled,
  });
  return { mark: (id: number, done: boolean) => mark.mutate({ id, done }), remove: (id: number) => remove.mutate(id) };
}
