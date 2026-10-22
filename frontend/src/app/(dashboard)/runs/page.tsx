"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight, ListTodo, Search } from "lucide-react";
import { api } from "@/lib/api";
import type { RunSummary } from "@/lib/types";
import {
  EmptyState,
  ErrorState,
  formatDuration,
  formatTime,
  LoadingRows,
  PageHeader,
  StatusBadge,
} from "@/components/shared";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const STATUSES = [
  "all",
  "queued",
  "running",
  "waiting_approval",
  "completed",
  "failed",
  "cancelled",
];

export default function RunsPage() {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("all");

  const runs = useQuery({
    queryKey: ["runs", q, status],
    queryFn: () =>
      api<{ items: RunSummary[]; total: number }>(
        `/runs?limit=50${q ? `&q=${encodeURIComponent(q)}` : ""}${
          status !== "all" ? `&status=${status}` : ""
        }`,
      ),
    refetchInterval: 10_000,
  });

  return (
    <div>
      <PageHeader
        title="Runs"
        description="Execution history with persisted state — reopening a run restores its full plan, timeline, and report."
      />
      <div className="mb-4 flex flex-wrap gap-2">
        <div className="relative">
          <Search
            className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search title or request…"
            className="w-72 bg-card pl-8"
            aria-label="Search runs"
          />
        </div>
        <Select value={status} onValueChange={(v) => setStatus(v ?? "all")}>
          <SelectTrigger className="w-44 bg-card" aria-label="Status filter">
            <span>{status === "all" ? "All statuses" : status.replace(/_/g, " ")}</span>
          </SelectTrigger>
          <SelectContent>
            {STATUSES.map((s) => (
              <SelectItem key={s} value={s}>
                {s === "all" ? "All statuses" : s.replace(/_/g, " ")}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {runs.data && (
          <p className="ml-auto self-center text-xs text-muted-foreground">
            {runs.data.total} run{runs.data.total === 1 ? "" : "s"}
          </p>
        )}
      </div>

      {runs.isLoading ? (
        <LoadingRows rows={6} />
      ) : runs.isError ? (
        <ErrorState message={runs.error.message} retry={() => runs.refetch()} />
      ) : runs.data!.items.length === 0 ? (
        <EmptyState
          title="No runs match"
          icon={ListTodo}
          hint={
            q || status !== "all"
              ? "Adjust the search or status filter."
              : "Submit a task in the Agent Workspace to see it here."
          }
        />
      ) : (
        <section className="overflow-x-auto rounded-lg border border-border bg-card">
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>Task</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="hidden md:table-cell">Agents</TableHead>
                <TableHead className="hidden sm:table-cell">Started</TableHead>
                <TableHead className="text-right">Duration</TableHead>
                <TableHead className="w-8" aria-label="Open" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {runs.data!.items.map((run) => (
                <TableRow
                  key={run.id}
                  className="cursor-pointer"
                  onClick={() => router.push(`/runs/${run.id}`)}
                >
                  <TableCell className="max-w-0 w-full">
                    <p className="truncate text-sm font-medium">{run.title}</p>
                    <p className="text-xs text-muted-foreground md:hidden">
                      {(run.agents ?? []).join(", ")}
                    </p>
                  </TableCell>
                  <TableCell>
                    <StatusBadge status={run.status} />
                  </TableCell>
                  <TableCell className="hidden md:table-cell">
                    <div className="flex flex-wrap gap-1">
                      {(run.agents ?? []).length === 0 ? (
                        <span className="text-xs text-muted-foreground">—</span>
                      ) : (
                        (run.agents ?? []).map((agent) => (
                          <span
                            key={agent}
                            className="rounded-full bg-secondary px-2 py-0.5 text-[11px] font-medium text-muted-foreground"
                          >
                            {agent}
                          </span>
                        ))
                      )}
                    </div>
                  </TableCell>
                  <TableCell className="hidden whitespace-nowrap text-xs text-muted-foreground sm:table-cell">
                    {formatTime(run.started_at ?? run.created_at)}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-right font-mono text-xs tabular-nums">
                    {formatDuration(run.started_at, run.finished_at)}
                  </TableCell>
                  <TableCell>
                    <ChevronRight className="size-4 text-muted-foreground/60" aria-hidden />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </section>
      )}
    </div>
  );
}
