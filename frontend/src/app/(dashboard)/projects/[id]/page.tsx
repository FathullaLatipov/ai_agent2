"use client";

import { use, useMemo, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  CheckCircle2,
  ChevronDown,
  Pause,
  Play,
  ShieldQuestion,
  Wifi,
  WifiOff,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";
import { api, useAuth } from "@/lib/api";
import type { ProjectDetail, ProjectGraph, ProjectPlan, RunEvent } from "@/lib/types";
import { useProjectEvents } from "@/hooks/use-project-events";
import {
  EmptyState,
  ErrorState,
  formatClock,
  LoadingRows,
  StatusBadge,
} from "@/components/shared";
import { ProjectGraphView } from "@/components/project/project-graph";
import { TaskBoard } from "@/components/project/task-board";
import { PMChat } from "@/components/project/pm-chat";
import { TaskDetailsDialog } from "@/components/project/task-details";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";

const ACTIVE_PROJECT = new Set(["active", "planning", "awaiting_approval"]);

export default function ProjectPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const queryClient = useQueryClient();
  const role = useAuth((s) => s.me?.memberships[0]?.role);
  const canDecide = role === "owner" || role === "admin";
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);

  const project = useQuery({
    queryKey: ["project", id],
    queryFn: () => api<ProjectDetail>(`/projects/${id}`),
    refetchInterval: (q) =>
      q.state.data && ACTIVE_PROJECT.has(q.state.data.status) ? 7000 : false,
  });
  const graph = useQuery({
    queryKey: ["project-graph", id],
    queryFn: () => api<ProjectGraph>(`/projects/${id}/graph`),
  });
  const isActive = project.data ? ACTIVE_PROJECT.has(project.data.status) : true;
  const { events, connected } = useProjectEvents(id, isActive);

  const lifecycle = useMutation({
    mutationFn: (action: "pause" | "resume" | "cancel") =>
      api(`/projects/${id}/${action}`, { method: "POST" }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["project", id] }),
    onError: (e) => toast.error(e.message),
  });

  const selectByOrdinal = useMemo(() => {
    const tasks = project.data?.tasks ?? [];
    return (ordinal: number) => {
      const task = tasks.find((t) => t.ordinal === ordinal);
      if (task) setSelectedTaskId(task.id);
    };
  }, [project.data?.tasks]);

  if (project.isLoading) return <LoadingRows rows={6} />;
  if (project.isError)
    return <ErrorState message={project.error.message} retry={() => project.refetch()} />;
  const detail = project.data!;
  const pendingPlan = detail.plans.find((p) => p.status === "proposed");
  const succeeded = detail.task_counts.succeeded ?? 0;
  const total = detail.tasks.length;

  return (
    <div className="space-y-4">
      <Link
        href="/projects"
        className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" aria-hidden /> All projects
      </Link>

      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="truncate text-xl font-semibold tracking-tight">{detail.name}</h1>
            <StatusBadge status={detail.status} />
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
          <p className="mt-0.5 max-w-2xl text-sm text-muted-foreground">{detail.goal}</p>
        </div>
        <div className="flex items-center gap-2">
          {total > 0 && (
            <span className="text-xs text-muted-foreground">
              {succeeded}/{total} tasks done
            </span>
          )}
          {detail.status === "active" && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => lifecycle.mutate("pause")}
              disabled={lifecycle.isPending}
            >
              <Pause className="size-4" aria-hidden /> Pause
            </Button>
          )}
          {detail.status === "paused" && (
            <Button
              size="sm"
              onClick={() => lifecycle.mutate("resume")}
              disabled={lifecycle.isPending}
            >
              <Play className="size-4" aria-hidden /> Resume
            </Button>
          )}
          {["active", "paused", "awaiting_approval", "draft"].includes(detail.status) && (
            <Button
              variant="ghost"
              size="sm"
              className="text-muted-foreground"
              onClick={() => {
                if (confirm("Cancel this project? Unfinished tasks will be cancelled.")) {
                  lifecycle.mutate("cancel");
                }
              }}
              disabled={lifecycle.isPending}
            >
              <XCircle className="size-4" aria-hidden /> Cancel
            </Button>
          )}
        </div>
      </div>

      {pendingPlan && (
        <PlanApprovalBanner projectId={id} plan={pendingPlan} canDecide={canDecide} />
      )}

      <div className="grid items-start gap-4 xl:grid-cols-3">
        <div className="min-w-0 space-y-4 xl:col-span-2">
          <Tabs defaultValue="workflow">
            <TabsList>
              <TabsTrigger value="workflow">Workflow</TabsTrigger>
              <TabsTrigger value="board">Board</TabsTrigger>
              <TabsTrigger value="timeline">Timeline</TabsTrigger>
            </TabsList>
            <TabsContent value="workflow">
              {graph.isLoading ? (
                <LoadingRows rows={3} />
              ) : graph.isError ? (
                <ErrorState message={graph.error.message} retry={() => graph.refetch()} />
              ) : (
                <ProjectGraphView graph={graph.data!} onSelect={selectByOrdinal} />
              )}
              <p className="mt-2 text-xs text-muted-foreground">
                The graph reflects persisted task state — click a node for details,
                dependencies, and actions.
              </p>
            </TabsContent>
            <TabsContent value="board">
              {detail.tasks.length === 0 ? (
                <EmptyState
                  title="No tasks yet"
                  hint="Ask the PM to create a plan, or add a task through the PM panel."
                />
              ) : (
                <TaskBoard tasks={detail.tasks} onSelect={selectByOrdinal} />
              )}
            </TabsContent>
            <TabsContent value="timeline">
              <ProjectTimeline events={events} />
            </TabsContent>
          </Tabs>
        </div>
        <PMChat projectId={id} />
      </div>

      <TaskDetailsDialog
        projectId={id}
        taskId={selectedTaskId}
        onClose={() => setSelectedTaskId(null)}
      />
    </div>
  );
}

function PlanApprovalBanner({
  projectId,
  plan,
  canDecide,
}: {
  projectId: string;
  plan: ProjectPlan;
  canDecide: boolean;
}) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(true);
  const decide = useMutation({
    mutationFn: (approve: boolean) =>
      api(`/projects/${projectId}/plans/${plan.version}/decide`, {
        method: "POST",
        body: JSON.stringify({ approve }),
      }),
    onSuccess: (_d, approve) => {
      toast.success(approve ? "Plan approved — execution starts" : "Plan rejected");
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["project-graph", projectId] });
    },
    onError: (e) => toast.error(e.message),
  });

  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="flex items-center gap-2 text-sm font-semibold text-amber-900">
            <ShieldQuestion className="size-4" aria-hidden />
            Plan v{plan.version} awaits your decision
          </p>
          <p className="mt-1 text-sm text-amber-900/80">{plan.goal}</p>
          <button
            className="mt-1.5 inline-flex items-center gap-1 text-xs font-medium text-amber-900/70 hover:text-amber-900"
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
          >
            {plan.tasks_spec.length} task(s)
            <ChevronDown
              className={cn("size-3 transition-transform duration-150", open && "rotate-180")}
              aria-hidden
            />
          </button>
          {open && (
            <ol className="mt-2 space-y-1">
              {plan.tasks_spec.map((spec) => (
                <li
                  key={spec.ref}
                  className="rounded-md border border-amber-200/70 bg-white/70 px-2.5 py-1.5 text-xs"
                >
                  <span className="font-medium">{spec.title}</span>
                  <span className="text-muted-foreground">
                    {" "}
                    — {spec.agent}
                    {spec.depends_on.length > 0 && ` · after ${spec.depends_on.join(", ")}`}
                  </span>
                </li>
              ))}
            </ol>
          )}
          {plan.assumptions.length > 0 && (
            <p className="mt-1.5 text-xs text-amber-900/60">
              Assumptions: {plan.assumptions.join("; ")}
            </p>
          )}
        </div>
        <div className="flex shrink-0 gap-2">
          {canDecide ? (
            <>
              <Button size="sm" onClick={() => decide.mutate(true)} disabled={decide.isPending}>
                <CheckCircle2 className="size-4" aria-hidden /> Approve
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="border-amber-300 bg-white"
                onClick={() => decide.mutate(false)}
                disabled={decide.isPending}
              >
                Reject
              </Button>
            </>
          ) : (
            <p className="text-xs text-amber-900/70">An admin or owner must decide.</p>
          )}
        </div>
      </div>
    </div>
  );
}

function ProjectTimeline({ events }: { events: RunEvent[] }) {
  if (events.length === 0)
    return <EmptyState title="No events yet" hint="Project activity appears here as it happens." />;
  return (
    <ol className="max-h-[26rem] space-y-0.5 overflow-y-auto rounded-lg border border-border bg-card p-3">
      {[...events].reverse().map((event) => (
        <li
          key={event.seq}
          className="flex items-baseline justify-between gap-3 border-b border-border/60 px-1 py-1.5 last:border-0"
        >
          <span className="min-w-0 truncate text-[13px]">
            <span className="font-medium">{event.type.replace(/[._]/g, " ")}</span>
            <span className="text-muted-foreground"> {summarize(event)}</span>
          </span>
          <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
            {formatClock(event.created_at)}
          </span>
        </li>
      ))}
    </ol>
  );
}

function summarize(event: RunEvent): string {
  const p = event.payload;
  const parts: string[] = [];
  if (p.ordinal !== undefined) parts.push(`#${p.ordinal}`);
  for (const key of ["title", "agent", "version", "reason", "error", "summary", "feedback"]) {
    const value = p[key];
    if (value) parts.push(String(value).slice(0, 80));
  }
  return parts.join(" · ");
}
