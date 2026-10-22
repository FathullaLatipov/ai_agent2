"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Ban,
  Bot,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Copy,
  Download,
  FileText,
  Loader2,
  RotateCcw,
  ShieldQuestion,
  Wifi,
  WifiOff,
  Wrench,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";
import { api, useAuth } from "@/lib/api";
import type { Approval, RunDetail, RunEvent, RunTask } from "@/lib/types";
import { useRunEvents } from "@/hooks/use-run-events";
import {
  EmptyState,
  ErrorState,
  formatClock,
  formatDuration,
  formatTime,
  LoadingRows,
  StatusBadge,
} from "@/components/shared";
import { Markdown } from "@/components/markdown";
import { WorkflowGraph } from "@/components/workflow-graph";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";

const ACTIVE_STATUSES = new Set([
  "queued",
  "planning",
  "running",
  "waiting_approval",
  "reviewing",
  "reporting",
  "cancelling",
]);

export function RunView({ runId }: { runId: string }) {
  const queryClient = useQueryClient();
  const role = useAuth((s) => s.me?.memberships[0]?.role);
  const canDecide = role === "owner" || role === "admin";

  const runQuery = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api<RunDetail>(`/runs/${runId}`),
    refetchInterval: (q) =>
      q.state.data && ACTIVE_STATUSES.has(q.state.data.status) ? 5000 : false,
  });
  const run = runQuery.data;
  const isActive = run ? ACTIVE_STATUSES.has(run.status) : true;
  const { events, connected } = useRunEvents(runId, isActive);

  const cancel = useMutation({
    mutationFn: () => api(`/runs/${runId}/cancel`, { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["run", runId] }),
    onError: (e) => toast.error(e.message),
  });
  const retry = useMutation({
    mutationFn: () => api(`/runs/${runId}/retry`, { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["run", runId] }),
    onError: (e) => toast.error(e.message),
  });
  const decide = useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: "approve" | "reject" }) =>
      api(`/approvals/${id}/decide`, {
        method: "POST",
        body: JSON.stringify({ decision }),
      }),
    onSuccess: (_d, vars) => {
      toast.success(`Operation ${vars.decision}d`);
      void queryClient.invalidateQueries({ queryKey: ["run", runId] });
      void queryClient.invalidateQueries({ queryKey: ["approvals"] });
    },
    onError: (e) => toast.error(e.message),
  });

  if (runQuery.isLoading) return <LoadingRows rows={6} />;
  if (runQuery.isError)
    return <ErrorState message={runQuery.error.message} retry={() => runQuery.refetch()} />;
  if (!run) return <EmptyState title="Run not found" />;

  const pendingApprovals = run.approvals.filter((a) => a.status === "pending");

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="truncate text-lg font-semibold tracking-tight">{run.title}</h2>
            <StatusBadge status={run.status} />
            <span className="rounded-full border border-border bg-card px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
              {run.mode === "llm" ? "LLM mode" : "deterministic"}
            </span>
            {isActive &&
              (connected ? (
                <span className="inline-flex items-center gap-1 text-xs font-medium text-emerald-600">
                  <Wifi className="size-3.5" aria-hidden /> live
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                  <WifiOff className="size-3.5" aria-hidden /> reconnecting…
                </span>
              ))}
          </div>
          <p className="mt-0.5 text-xs text-muted-foreground">
            created {formatTime(run.created_at)} · duration{" "}
            {formatDuration(run.started_at, run.finished_at)}
          </p>
        </div>
        <div className="flex gap-2">
          {run.report && (
            <Button
              variant="outline"
              size="sm"
              onClick={async () => {
                const text = await api<string>(`/runs/${run.id}/report.md`, { raw: true });
                const blob = new Blob([text], { type: "text/markdown" });
                const url = URL.createObjectURL(blob);
                const a = document.createElement("a");
                a.href = url;
                a.download = `report-${run.id}.md`;
                a.click();
                URL.revokeObjectURL(url);
              }}
            >
              <Download className="size-4" aria-hidden /> Download report
            </Button>
          )}
          {isActive && run.status !== "cancelling" && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => cancel.mutate()}
              disabled={cancel.isPending}
            >
              <Ban className="size-4" aria-hidden /> Cancel
            </Button>
          )}
          {run.status === "failed" && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => retry.mutate()}
              disabled={retry.isPending}
            >
              <RotateCcw className="size-4" aria-hidden /> Retry
            </Button>
          )}
        </div>
      </div>

      {run.error && (
        <div className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-3.5 py-2.5 text-sm text-red-700">
          <XCircle className="mt-0.5 size-4 shrink-0" aria-hidden />
          {run.error}
        </div>
      )}

      {pendingApprovals.map((approval) => (
        <ApprovalBanner
          key={approval.id}
          approval={approval}
          canDecide={canDecide}
          onDecide={(decision) => decide.mutate({ id: approval.id, decision })}
          busy={decide.isPending}
        />
      ))}

      {run.plan ? <WorkflowGraph run={run} /> : null}

      <Tabs defaultValue="activity">
        <TabsList>
          <TabsTrigger value="activity">Activity</TabsTrigger>
          <TabsTrigger value="tools">Tool calls ({run.tool_calls.length})</TabsTrigger>
          <TabsTrigger value="result">Result</TabsTrigger>
          <TabsTrigger value="plan">Plan</TabsTrigger>
        </TabsList>

        <TabsContent value="activity" className="grid items-start gap-4 lg:grid-cols-2">
          <section className="rounded-lg border border-border bg-card">
            <h3 className="border-b border-border px-4 py-2.5 text-sm font-semibold">
              Agent tasks
            </h3>
            <div className="space-y-2 p-3">
              {run.tasks.length === 0 && (
                <p className="px-1 py-2 text-sm text-muted-foreground">Plan not created yet.</p>
              )}
              {run.tasks.map((task) => (
                <TaskCard key={task.id} task={task} />
              ))}
            </div>
          </section>
          <section className="rounded-lg border border-border bg-card">
            <h3 className="border-b border-border px-4 py-2.5 text-sm font-semibold">
              Execution timeline
            </h3>
            <div className="p-3">
              <Timeline events={events} />
            </div>
          </section>
        </TabsContent>

        <TabsContent value="tools">
          <section className="rounded-lg border border-border bg-card">
            {run.tool_calls.length === 0 ? (
              <div className="p-4">
                <EmptyState title="No tool calls yet" icon={Wrench} />
              </div>
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow className="hover:bg-transparent">
                      <TableHead>Tool</TableHead>
                      <TableHead>Agent</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead className="text-right">Duration</TableHead>
                      <TableHead>Started</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {run.tool_calls.map((call) => (
                      <TableRow key={call.id}>
                        <TableCell className="font-mono text-xs">{call.tool_name}</TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {call.agent_key}
                        </TableCell>
                        <TableCell>
                          <StatusBadge status={call.status} />
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs tabular-nums">
                          {call.duration_ms != null ? `${call.duration_ms} ms` : "—"}
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {formatTime(call.created_at)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </section>
        </TabsContent>

        <TabsContent value="result">
          <ResultPanel run={run} />
        </TabsContent>

        <TabsContent value="plan">
          <PlanPanel run={run} />
        </TabsContent>
      </Tabs>

      <p className="text-xs text-muted-foreground">
        Usage: LLM calls {run.llm_calls} · tokens in {run.tokens_input ?? "not reported"} ·
        tokens out {run.tokens_output ?? "not reported"}
      </p>
    </div>
  );
}

/* ---------- Plan ---------- */

function PlanPanel({ run }: { run: RunDetail }) {
  return (
    <section className="rounded-lg border border-border bg-card p-4">
      {run.plan ? (
        <div className="space-y-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Goal
            </p>
            <p className="mt-0.5 text-sm">{run.plan.goal}</p>
          </div>
          {run.plan.notes && (
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Planner notes
              </p>
              <p className="mt-0.5 text-sm text-muted-foreground">{run.plan.notes}</p>
            </div>
          )}
          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Steps
            </p>
            <ol className="space-y-2">
              {run.plan.tasks.map((t) => {
                const live = run.tasks.find((task) => task.ordinal === t.ordinal);
                return (
                  <li
                    key={t.ordinal}
                    className="flex items-start gap-3 rounded-md border border-border px-3 py-2.5"
                  >
                    <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-secondary font-mono text-[11px] font-semibold text-muted-foreground">
                      {t.ordinal}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="text-sm">
                        <span className="font-medium capitalize">{t.agent}</span>
                        <span className="text-muted-foreground"> — {t.objective}</span>
                      </p>
                      {t.depends_on.length > 0 && (
                        <p className="mt-0.5 text-xs text-muted-foreground">
                          depends on step {t.depends_on.join(", ")}
                        </p>
                      )}
                      {Object.keys(t.params).length > 0 && (
                        <pre className="mt-1.5 overflow-x-auto rounded-md bg-secondary/70 p-2 font-mono text-[11px] text-foreground/80">
                          {JSON.stringify(t.params, null, 2)}
                        </pre>
                      )}
                    </div>
                    {live && <StatusBadge status={live.status} className="shrink-0" />}
                  </li>
                );
              })}
            </ol>
          </div>
          <p className="border-t border-border pt-3 text-xs text-muted-foreground">
            Original request: {run.input_text}
          </p>
        </div>
      ) : (
        <EmptyState title="Plan not created yet" icon={FileText} />
      )}
    </section>
  );
}

/* ---------- Result ---------- */

function ResultPanel({ run }: { run: RunDetail }) {
  const [copied, setCopied] = useState(false);
  const isActive = ACTIVE_STATUSES.has(run.status);
  if (!run.report) {
    return (
      <section className="rounded-lg border border-border bg-card p-4">
        <EmptyState
          title="No report yet"
          icon={FileText}
          hint={
            isActive
              ? "The report is generated when the run completes."
              : "This run ended without producing a report."
          }
        />
      </section>
    );
  }
  return (
    <section className="rounded-lg border border-border bg-card">
      <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-2.5">
        <p className="text-sm font-semibold">{run.report.title}</p>
        <div className="flex items-center gap-1.5">
          <Button
            variant="ghost"
            size="sm"
            onClick={async () => {
              await navigator.clipboard.writeText(run.report!.body_markdown);
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            }}
          >
            {copied ? (
              <Check className="size-4 text-emerald-600" aria-hidden />
            ) : (
              <Copy className="size-4" aria-hidden />
            )}
            {copied ? "Copied" : "Copy"}
          </Button>
        </div>
      </div>
      <div className="mx-auto max-w-3xl px-5 py-5 md:px-8">
        <Markdown>{run.report.body_markdown}</Markdown>
      </div>
      <p className="border-t border-border px-4 py-2 text-xs text-muted-foreground">
        Generated {formatTime(run.report.created_at)} · format {run.report.format} · mode{" "}
        {run.mode}
      </p>
    </section>
  );
}

/* ---------- Tasks ---------- */

function TaskCard({ task }: { task: RunTask }) {
  const summary = typeof task.output?.summary === "string" ? task.output.summary : null;
  return (
    <div className="rounded-md border border-border px-3 py-2.5 transition-colors duration-150 hover:border-[#D6D9E0]">
      <div className="flex items-center justify-between gap-2">
        <p className="min-w-0 truncate text-sm font-medium">
          <span className="mr-1.5 font-mono text-xs text-muted-foreground">#{task.ordinal}</span>
          <span className="capitalize">{task.agent_key}</span>
          {task.attempts > 1 && (
            <span className="ml-2 text-xs font-normal text-amber-700">
              attempt {task.attempts}
            </span>
          )}
        </p>
        <StatusBadge status={task.status} />
      </div>
      <p className="mt-0.5 text-xs text-muted-foreground">{task.objective}</p>
      {summary && (
        <p className="mt-2 border-l-2 border-[#C8CDFB] pl-2.5 text-xs leading-relaxed text-foreground/80">
          {summary}
        </p>
      )}
      {task.error && <p className="mt-2 text-xs text-red-600">{task.error}</p>}
    </div>
  );
}

/* ---------- Approval banner ---------- */

function ApprovalBanner({
  approval,
  canDecide,
  onDecide,
  busy,
}: {
  approval: Approval;
  canDecide: boolean;
  onDecide: (d: "approve" | "reject") => void;
  busy: boolean;
}) {
  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex items-center gap-2 text-sm font-semibold text-amber-900">
            <ShieldQuestion className="size-4" aria-hidden />
            Human approval required
          </p>
          <p className="mt-1 text-sm text-amber-900/80">
            Agent <span className="font-medium">{approval.requested_by_agent}</span> wants to
            execute <span className="font-mono text-[13px]">{approval.tool_name}</span> —{" "}
            {approval.operation_summary}
          </p>
          {approval.payload.arguments && (
            <pre className="mt-2 max-h-40 overflow-auto rounded-md border border-amber-200/70 bg-white/70 p-2.5 font-mono text-[11px] text-foreground/80">
              {JSON.stringify(approval.payload.arguments, null, 2)}
            </pre>
          )}
          <p className="mt-1.5 text-xs text-amber-900/60">
            Approving executes the operation immediately. Rejecting fails this task.
          </p>
        </div>
        <div className="flex shrink-0 gap-2">
          {canDecide ? (
            <>
              <Button size="sm" onClick={() => onDecide("approve")} disabled={busy}>
                Approve
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="border-amber-300 bg-white"
                onClick={() => onDecide("reject")}
                disabled={busy}
              >
                Reject
              </Button>
            </>
          ) : (
            <p className="text-xs text-amber-900/70">
              Waiting for an admin or owner to decide.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

/* ---------- Timeline ---------- */

const EVENT_META: Record<string, { icon: React.ElementType; className: string }> = {
  "run.created": { icon: FileText, className: "bg-secondary text-muted-foreground" },
  "run.started": { icon: Loader2, className: "bg-blue-50 text-blue-600" },
  "run.plan_created": { icon: FileText, className: "bg-blue-50 text-blue-600" },
  "run.completed": { icon: CheckCircle2, className: "bg-emerald-50 text-emerald-600" },
  "run.failed": { icon: XCircle, className: "bg-red-50 text-red-600" },
  "run.cancelled": { icon: Ban, className: "bg-secondary text-muted-foreground" },
  "task.started": { icon: Bot, className: "bg-blue-50 text-blue-600" },
  "task.completed": { icon: CheckCircle2, className: "bg-emerald-50 text-emerald-600" },
  "task.failed": { icon: XCircle, className: "bg-red-50 text-red-600" },
  "task.retrying": { icon: RotateCcw, className: "bg-amber-50 text-amber-600" },
  "task.waiting_approval": { icon: ShieldQuestion, className: "bg-amber-50 text-amber-600" },
  "tool.called": { icon: Wrench, className: "bg-secondary text-muted-foreground" },
  "tool.completed": { icon: Wrench, className: "bg-emerald-50 text-emerald-600" },
  "tool.failed": { icon: AlertTriangle, className: "bg-red-50 text-red-600" },
  "approval.requested": { icon: ShieldQuestion, className: "bg-amber-50 text-amber-600" },
  "approval.decided": { icon: CheckCircle2, className: "bg-amber-50 text-amber-700" },
  "review.completed": { icon: CheckCircle2, className: "bg-blue-50 text-blue-600" },
};

function eventTitle(event: RunEvent): string {
  const p = event.payload;
  switch (event.type) {
    case "task.started":
      return `${p.agent} started (step ${p.ordinal}${Number(p.attempt) > 1 ? `, attempt ${p.attempt}` : ""})`;
    case "task.completed":
      return `${p.agent} completed (step ${p.ordinal})`;
    case "task.failed":
      return `${p.agent} failed (step ${p.ordinal})`;
    case "task.retrying":
      return `retrying step ${p.ordinal} (attempt ${p.attempt})`;
    case "task.waiting_approval":
      return `step ${p.ordinal} waiting for approval`;
    case "tool.called":
      return `${p.tool} invoked by ${p.agent}`;
    case "tool.completed":
      return `${p.tool} completed${p.duration_ms != null ? ` in ${p.duration_ms} ms` : ""}`;
    case "tool.failed":
      return `${p.tool} ${String(p.status ?? "failed").replace(/_/g, " ")}`;
    case "approval.requested":
      return `approval requested for ${p.tool}`;
    case "approval.decided":
      return `approval ${p.decision}`;
    case "review.completed":
      return `review ${p.approved ? "passed" : "requested changes"}`;
    case "run.status_changed":
      return `status: ${p.from} → ${p.to}`;
    case "llm.called":
      return `LLM call (${p.agent})`;
    default:
      return event.type.replace(/[._]/g, " ");
  }
}

function Timeline({ events }: { events: RunEvent[] }) {
  if (events.length === 0)
    return <p className="px-1 py-2 text-sm text-muted-foreground">No events yet.</p>;
  return (
    <ol className="max-h-[26rem] overflow-y-auto pr-1" aria-live="polite">
      {events.map((event, i) => (
        <TimelineRow key={event.seq} event={event} isLast={i === events.length - 1} />
      ))}
    </ol>
  );
}

function TimelineRow({ event, isLast }: { event: RunEvent; isLast: boolean }) {
  const [open, setOpen] = useState(false);
  const meta = EVENT_META[event.type] ?? {
    icon: ChevronRight,
    className: "bg-secondary text-muted-foreground",
  };
  const Icon = meta.icon;
  const hasDetails = Object.keys(event.payload ?? {}).length > 0;
  return (
    <li className="relative flex gap-3 pb-0.5">
      {!isLast && (
        <span className="absolute left-[11px] top-6 h-full w-px bg-border" aria-hidden />
      )}
      <span
        className={cn(
          "z-10 mt-1 flex size-[23px] shrink-0 items-center justify-center rounded-full border border-border bg-card",
        )}
      >
        <span className={cn("flex size-[17px] items-center justify-center rounded-full", meta.className)}>
          <Icon className="size-3" aria-hidden />
        </span>
      </span>
      <div className="min-w-0 flex-1 pb-2.5">
        <button
          className={cn(
            "flex w-full items-baseline justify-between gap-2 rounded px-1 py-0.5 text-left",
            hasDetails && "transition-colors duration-150 hover:bg-secondary/60",
          )}
          onClick={() => hasDetails && setOpen((o) => !o)}
          aria-expanded={hasDetails ? open : undefined}
        >
          <span className="min-w-0 truncate text-[13px] text-foreground/90">
            {eventTitle(event)}
          </span>
          <span className="flex shrink-0 items-center gap-1 font-mono text-[11px] text-muted-foreground">
            {formatClock(event.created_at)}
            {hasDetails && (
              <ChevronDown
                className={cn("size-3 transition-transform duration-150", open && "rotate-180")}
                aria-hidden
              />
            )}
          </span>
        </button>
        {open && hasDetails && (
          <pre className="mt-1 max-h-48 overflow-auto rounded-md bg-secondary/70 p-2 font-mono text-[11px] text-foreground/75">
            {JSON.stringify(event.payload, null, 2)}
          </pre>
        )}
      </div>
    </li>
  );
}
