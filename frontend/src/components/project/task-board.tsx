"use client";

import type { PMTask } from "@/lib/types";
import { StatusBadge } from "@/components/shared";

const COLUMNS: { label: string; statuses: string[] }[] = [
  { label: "Planned", statuses: ["draft", "planned"] },
  { label: "Blocked", statuses: ["blocked", "needs_revision"] },
  { label: "Ready", statuses: ["ready", "waiting_approval"] },
  { label: "In progress", statuses: ["assigned", "running", "reviewing"] },
  { label: "Done", statuses: ["succeeded"] },
  { label: "Failed", statuses: ["failed", "cancelled"] },
];

export function TaskBoard({
  tasks,
  onSelect,
}: {
  tasks: PMTask[];
  onSelect: (ordinal: number) => void;
}) {
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
      {COLUMNS.map((column) => {
        const items = tasks
          .filter((t) => column.statuses.includes(t.status))
          .sort((a, b) => a.priority - b.priority || a.ordinal - b.ordinal);
        return (
          <div key={column.label} className="rounded-lg border border-border bg-secondary/40">
            <p className="flex items-center justify-between border-b border-border px-2.5 py-2 text-xs font-semibold text-muted-foreground">
              {column.label}
              <span className="font-mono tabular-nums">{items.length}</span>
            </p>
            <div className="space-y-1.5 p-1.5">
              {items.length === 0 && (
                <p className="px-1 py-2 text-center text-[11px] text-muted-foreground/60">—</p>
              )}
              {items.map((task) => (
                <button
                  key={task.id}
                  onClick={() => onSelect(task.ordinal)}
                  className="w-full rounded-md border border-border bg-card px-2.5 py-2 text-left shadow-[0_1px_1px_rgba(16,24,40,0.04)] transition-colors duration-150 hover:border-[#C8CDFB]"
                >
                  <p className="text-xs font-medium">
                    <span className="mr-1 font-mono text-[10px] text-muted-foreground">
                      #{task.ordinal}
                    </span>
                    {task.title}
                  </p>
                  <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                    <StatusBadge status={task.status} className="px-1.5 text-[10px]" />
                    {task.agent_key && (
                      <span className="rounded-full bg-secondary px-1.5 py-0.5 text-[10px] text-muted-foreground">
                        {task.agent_key}
                      </span>
                    )}
                    {task.depends_on.length > 0 && (
                      <span className="text-[10px] text-muted-foreground">
                        after {task.depends_on.map((d) => `#${d}`).join(", ")}
                      </span>
                    )}
                  </div>
                  {task.blocked_reason && (
                    <p className="mt-1 line-clamp-2 text-[10px] text-orange-700">
                      {task.blocked_reason}
                    </p>
                  )}
                  {task.error && (
                    <p className="mt-1 line-clamp-2 text-[10px] text-red-600">{task.error}</p>
                  )}
                </button>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
