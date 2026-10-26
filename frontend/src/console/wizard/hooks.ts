import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { jobsApi, type JobEvent, type Stage } from "../api";
import { describeError } from "../ui/states";
import { useToast } from "../ui/toast";
import { mergeEvents } from "./steps";

const EMPTY: JobEvent[] = [];

/**
 * The events of a job, live. The server streams them as Server-Sent Events: the whole history first, then new ones as they happen, and
 * an `end` event when the job reaches a final state. A browser reconnects by itself after the stream times out and resumes from the
 * last event it saw. Where EventSource does not exist (or the stream fails for good) the list is polled instead.
 */
export function useJobEvents(jobId: string | undefined, enabled = true): { events: JobEvent[]; live: boolean; reconnect: () => void } {
  // The events belong to one job: they are kept with its id, so that another job starts from an empty list without an effect to clear it.
  const [store, setStore] = useState<{ id: string | undefined; events: JobEvent[] }>({ id: undefined, events: [] });
  const [live, setLive] = useState(false);
  const [epoch, setEpoch] = useState(0);
  const cursor = useRef(0);
  const following = useRef<string | undefined>(undefined);
  const events = store.id === jobId ? store.events : EMPTY;

  useEffect(() => {
    if (!jobId || !enabled) return;
    if (following.current !== jobId) {
      following.current = jobId;
      cursor.current = 0;
    }
    let closed = false;
    let source: EventSource | null = null;
    let timer: number | undefined;

    const take = (incoming: JobEvent[]) => {
      if (closed || incoming.length === 0) return;
      cursor.current = Math.max(cursor.current, ...incoming.map((e) => e.id));
      setStore((current) => ({ id: jobId, events: mergeEvents(current.id === jobId ? current.events : [], incoming) }));
    };

    const poll = () => {
      const controller = new AbortController();
      jobsApi
        .events(jobId, cursor.current, controller.signal)
        .then((page) => take(page.items))
        .catch(() => undefined)
        .finally(() => {
          if (!closed) timer = window.setTimeout(poll, 2000);
        });
    };

    if (typeof EventSource === "undefined") {
      poll();
    } else {
      source = new EventSource(jobsApi.streamUrl(jobId, cursor.current), { withCredentials: true });
      source.onopen = () => setLive(true);
      source.addEventListener("job", (message) => {
        try {
          take([JSON.parse((message as MessageEvent<string>).data) as JobEvent]);
        } catch {
          /* a malformed message is skipped */
        }
      });
      source.addEventListener("end", () => {
        setLive(false);
        source?.close();
      });
      source.onerror = () => {
        setLive(false);
        if (source?.readyState === EventSource.CLOSED && !closed) {
          source.close();
          poll();
        }
      };
    }
    return () => {
      closed = true;
      setLive(false);
      source?.close();
      if (timer) window.clearTimeout(timer);
    };
  }, [jobId, enabled, epoch]);

  const reconnect = useCallback(() => setEpoch((n) => n + 1), []);
  return { events, live, reconnect };
}

/** Starts stages in the background and refreshes what depends on the job. Errors are explained in a toast. */
export function useStageRunner(jobId: string | undefined, reconnect: () => void) {
  const client = useQueryClient();
  const toast = useToast();
  const refresh = useCallback(() => {
    void client.invalidateQueries({ queryKey: ["job", jobId] });
    void client.invalidateQueries({ queryKey: ["jobs"] });
    void client.invalidateQueries({ queryKey: ["overview"] });
  }, [client, jobId]);
  return useMutation({
    mutationFn: ({ stages, targetSchema }: { stages: Stage[]; targetSchema?: string }) => {
      if (!jobId) throw new Error("There is no migration yet");
      reconnect();
      return stages.length === 1 && stages[0] === "load" ? jobsApi.runStage(jobId, "load") : jobsApi.runStages(jobId, stages, targetSchema);
    },
    onSuccess: refresh,
    onError: (error) => {
      toast.error("The stage could not be started", describeError(error));
      refresh();
    },
  });
}
