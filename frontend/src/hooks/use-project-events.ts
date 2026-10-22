"use client";

import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { API_URL, api } from "@/lib/api";
import type { RunEvent } from "@/lib/types";

const EVENT_TYPES = [
  "project.created",
  "project.paused",
  "project.resumed",
  "project.cancelled",
  "project.completed",
  "project.attention",
  "plan.proposed",
  "plan.approved",
  "plan.rejected",
  "task.created",
  "task.assigned",
  "task.ready",
  "task.blocked",
  "task.started",
  "task.waiting_approval",
  "task.succeeded",
  "task.failed",
  "task.retrying",
  "task.retried",
  "task.review_failed",
  "task.revision_created",
  "task.recovered",
  "task.cancelled",
  "pm.message",
] as const;

// Anything that changes project/task/plan state refreshes the detail query.
const REFRESH_PREFIXES = ["project.", "plan.", "task."];

/** Live project events: REST replay + SSE with token-refreshing reconnect. */
export function useProjectEvents(projectId: string | null, active: boolean) {
  const [store, setStore] = useState<{ key: string | null; items: RunEvent[] }>({
    key: projectId,
    items: [],
  });
  const [connected, setConnected] = useState(false);
  const lastSeq = useRef(0);
  const queryClient = useQueryClient();

  if (store.key !== projectId) {
    setStore({ key: projectId, items: [] });
  }

  useEffect(() => {
    lastSeq.current = 0;
    if (!projectId) return;

    let closed = false;
    let source: EventSource | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    const append = (event: RunEvent) => {
      if (event.seq <= lastSeq.current) return;
      lastSeq.current = event.seq;
      setStore((prev) =>
        prev.key === projectId ? { key: prev.key, items: [...prev.items, event] } : prev,
      );
      if (REFRESH_PREFIXES.some((p) => event.type.startsWith(p))) {
        void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
        void queryClient.invalidateQueries({ queryKey: ["project-graph", projectId] });
        void queryClient.invalidateQueries({ queryKey: ["approvals"] });
      }
      if (event.type === "pm.message") {
        void queryClient.invalidateQueries({ queryKey: ["pm-messages", projectId] });
      }
    };

    void api<RunEvent[]>(`/projects/${projectId}/events`).then((history) => {
      if (closed) return;
      history.forEach(append);
      if (active) void openStream();
    });

    async function openStream() {
      if (closed) return;
      try {
        const { token } = await api<{ token: string }>(
          `/projects/${projectId}/stream-token`,
          { method: "POST" },
        );
        if (closed) return;
        const url = `${API_URL}/api/v1/projects/${projectId}/events/stream?token=${encodeURIComponent(token)}&after=${lastSeq.current}`;
        source = new EventSource(url);
        source.onopen = () => setConnected(true);
        for (const type of EVENT_TYPES) {
          source.addEventListener(type, (raw) => {
            append(JSON.parse((raw as MessageEvent).data) as RunEvent);
          });
        }
        source.onerror = () => {
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
  }, [projectId, active, queryClient]);

  return { events: store.items, connected };
}
