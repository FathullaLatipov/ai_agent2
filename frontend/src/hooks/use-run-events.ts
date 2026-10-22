"use client";

import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { API_URL, api } from "@/lib/api";
import type { RunEvent } from "@/lib/types";

// Named SSE event types emitted by the backend (see app/events + engine).
const EVENT_TYPES = [
  "run.created",
  "run.started",
  "run.status_changed",
  "run.plan_created",
  "run.completed",
  "run.failed",
  "run.cancelled",
  "run.cancel_requested",
  "run.retried",
  "task.started",
  "task.completed",
  "task.failed",
  "task.retrying",
  "task.waiting_approval",
  "tool.called",
  "tool.completed",
  "tool.failed",
  "approval.requested",
  "approval.decided",
  "review.completed",
  "llm.called",
] as const;

const TERMINAL = new Set(["run.completed", "run.failed", "run.cancelled"]);
// Events that change run/task/approval state shown elsewhere on the page.
const REFRESH_ON = new Set([
  "run.status_changed",
  "run.plan_created",
  "run.completed",
  "run.failed",
  "run.cancelled",
  "task.completed",
  "task.failed",
  "task.waiting_approval",
  "approval.requested",
  "approval.decided",
  "review.completed",
]);

/**
 * Live run events: replays persisted history (REST), then streams via SSE.
 * Stream tokens are short-lived, so reconnection mints a fresh token and
 * resumes from the last delivered seq — no events are lost or duplicated.
 */
export function useRunEvents(runId: string | null, active: boolean) {
  // Events are stored keyed by run id; switching runs resets during render
  // (the "derive state from props" pattern) instead of inside the effect.
  const [store, setStore] = useState<{ key: string | null; items: RunEvent[] }>({
    key: runId,
    items: [],
  });
  const [connected, setConnected] = useState(false);
  const lastSeq = useRef(0);
  const queryClient = useQueryClient();

  if (store.key !== runId) {
    // lastSeq is reset by the effect below before any stream opens.
    setStore({ key: runId, items: [] });
  }

  useEffect(() => {
    lastSeq.current = 0;
    if (!runId) return;

    let closed = false;
    let source: EventSource | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    const append = (event: RunEvent) => {
      if (event.seq <= lastSeq.current) return;
      lastSeq.current = event.seq;
      setStore((prev) =>
        prev.key === runId ? { key: prev.key, items: [...prev.items, event] } : prev,
      );
      if (REFRESH_ON.has(event.type)) {
        void queryClient.invalidateQueries({ queryKey: ["run", runId] });
        void queryClient.invalidateQueries({ queryKey: ["approvals"] });
      }
    };

    // Always load the persisted history first (works for finished runs too).
    void api<RunEvent[]>(`/runs/${runId}/events`).then((history) => {
      if (closed) return;
      history.forEach(append);
      if (!active) return;
      void openStream();
    });

    async function openStream() {
      if (closed) return;
      try {
        const { token } = await api<{ token: string }>(`/runs/${runId}/stream-token`, {
          method: "POST",
        });
        if (closed) return;
        const url = `${API_URL}/api/v1/runs/${runId}/events/stream?token=${encodeURIComponent(
          token,
        )}&after=${lastSeq.current}`;
        source = new EventSource(url);
        source.onopen = () => setConnected(true);
        for (const type of EVENT_TYPES) {
          source.addEventListener(type, (raw) => {
            const data = JSON.parse((raw as MessageEvent).data) as RunEvent;
            append(data);
            if (TERMINAL.has(data.type)) {
              source?.close();
              setConnected(false);
            }
          });
        }
        source.onerror = () => {
          // Token likely expired or connection dropped: reconnect with a fresh
          // token from the last seq (unless the run already finished).
          source?.close();
          setConnected(false);
          if (!closed) retryTimer = setTimeout(() => void openStream(), 2000);
        };
      } catch {
        if (!closed) retryTimer = setTimeout(() => void openStream(), 4000);
      }
    }

    return () => {
      closed = true;
      if (retryTimer) clearTimeout(retryTimer);
      source?.close();
      setConnected(false);
    };
  }, [runId, active, queryClient]);

  return { events: store.items, connected };
}
