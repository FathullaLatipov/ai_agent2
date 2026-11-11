"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  Activity,
  ArrowUpRight,
  BookOpen,
  CheckCircle2,
  CheckSquare,
  FolderKanban,
  ListTodo,
  Plus,
  ScrollText,
  Timer,
  Wrench,
  X,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";
import { api, useAuth } from "@/lib/api";
import type { Overview, RunSummary } from "@/lib/types";
import {
  EmptyState,
  ErrorState,
  formatTime,
  LoadingRows,
  StatTile,
  StatusBadge,
} from "@/components/shared";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableRow } from "@/components/ui/table";

const GUIDE_KEY = "agentos-guide-dismissed";

const GUIDE_STEPS = [
  {
    title: "Add your data",
    text: "Upload a CSV or company documents in the Knowledge Base — or drop a file straight onto the task card in the Agent Workspace.",
    href: "/knowledge",
    icon: BookOpen,
    cta: "Open Knowledge Base",
  },
  {
    title: "Describe the goal",
    text: "Create a project in your own words. The project manager agent drafts a step-by-step plan and picks the right agents for each task.",
    href: "/projects",
    icon: FolderKanban,
    cta: "Create a project",
  },
  {
    title: "Approve and watch",
    text: "Nothing runs until you approve the plan. Risky external actions (like sending a message) pause and wait for your decision in Approvals.",
    href: "/approvals",
    icon: CheckSquare,
    cta: "Review approvals",
  },
  {
    title: "Get the verified report",
    text: "A reviewer agent checks every result before it reaches you. Follow execution live and read the final report in Runs.",
    href: "/runs",
    icon: ScrollText,
    cta: "See your runs",
  },
] as const;

function greeting(): string {
  const hour = new Date().getHours();
  if (hour < 5) return "Good night";
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

const TEMPLATES = [
  {
    title: "Sales analysis",
    prompt:
      "Analyze the last 30 days of sales, identify underperforming products, and prepare recommendations",
  },
  {
    title: "Policy question",
    prompt: "What does our travel policy say about business class flights?",
  },
  {
    title: "CRM follow-up",
    prompt: "Prepare a customer follow-up for CUST-1001 about the open renewal and send it",
  },
];

export default function OverviewPage() {
  const router = useRouter();
  const me = useAuth((s) => s.me);
  const [guideHidden, setGuideHidden] = useState(() => {
    try {
      return localStorage.getItem(GUIDE_KEY) === "1";
    } catch {
      return false;
    }
  });
  const setGuide = (hidden: boolean) => {
    setGuideHidden(hidden);
    try {
      localStorage.setItem(GUIDE_KEY, hidden ? "1" : "0");
    } catch {
      /* private mode — the guide just won't persist */
    }
  };
  const overview = useQuery({
    queryKey: ["overview"],
    queryFn: () => api<Overview>("/meta/overview"),
    refetchInterval: 20_000,
  });

  const quickRun = useMutation({
    mutationFn: (input_text: string) =>
      api<RunSummary>("/runs", { method: "POST", body: JSON.stringify({ input_text }) }),
    onSuccess: (run) => router.push(`/runs/${run.id}`),
    onError: (e) => toast.error(e.message),
  });

  if (overview.isLoading) return <LoadingRows rows={6} />;
  if (overview.isError)
    return <ErrorState message={overview.error.message} retry={() => overview.refetch()} />;
  const data = overview.data!;
  const completed = data.runs_by_status.completed ?? 0;
  const failed = data.runs_by_status.failed ?? 0;
  const activeCount =
    (data.runs_by_status.running ?? 0) +
    (data.runs_by_status.planning ?? 0) +
    (data.runs_by_status.queued ?? 0) +
    (data.runs_by_status.waiting_approval ?? 0);

  return (
    <div className="space-y-6">
      {/* Heading */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-[30px] font-semibold leading-9 tracking-[-0.015em]">
            {greeting()}
            {me?.user.full_name ? `, ${me.user.full_name.split(" ")[0]}` : ""}
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {me?.memberships[0]?.organization.name ?? "Your workspace"} — every figure below
            comes from persisted execution data.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {guideHidden && (
            <Button variant="secondary" onClick={() => setGuide(false)}>
              How it works
            </Button>
          )}
          <Button onClick={() => router.push("/workspace")}>
            <Plus className="size-4" aria-hidden /> New task
          </Button>
        </div>
      </div>

      {/* Getting started — a real sequence, so the steps are numbered */}
      {!guideHidden && (
        <section className="relative rounded-2xl border border-border bg-card p-5 shadow-[0_1px_3px_rgba(23,25,35,0.05)]">
          <button
            type="button"
            aria-label="Hide the guide"
            onClick={() => setGuide(true)}
            className="absolute top-3.5 right-3.5 rounded-md p-1 text-muted-foreground transition-colors duration-150 hover:bg-secondary hover:text-foreground"
          >
            <X className="size-4" aria-hidden />
          </button>
          <h2 className="text-base font-semibold">How AgentOS works</h2>
          <p className="mt-0.5 text-sm text-muted-foreground">
            Four steps from your data to a verified result.
          </p>
          <ol className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {GUIDE_STEPS.map((step, i) => (
              <li
                key={step.title}
                className="flex flex-col rounded-xl border border-border bg-background/60 p-4"
              >
                <div className="flex items-center gap-2.5">
                  <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary text-[13px] font-semibold text-primary-foreground">
                    {i + 1}
                  </span>
                  <p className="text-sm font-semibold">{step.title}</p>
                </div>
                <p className="mt-2 flex-1 text-[13px] leading-relaxed text-muted-foreground">
                  {step.text}
                </p>
                <Link
                  href={step.href}
                  className="mt-3 inline-flex items-center gap-1 text-[13px] font-medium text-primary transition-colors duration-150 hover:text-[#4F48D6]"
                >
                  <step.icon className="size-3.5" aria-hidden />
                  {step.cta}
                </Link>
              </li>
            ))}
          </ol>
        </section>
      )}

      {/* Metrics */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Total runs" value={data.runs_total} icon={ListTodo} tone="accent" />
        <StatTile
          label="Successful"
          value={completed}
          icon={CheckCircle2}
          hint={activeCount > 0 ? `${activeCount} currently active` : undefined}
        />
        <StatTile label="Failed" value={failed} icon={XCircle} />
        <StatTile
          label="Avg duration"
          value={
            data.avg_completed_duration_s != null
              ? `${data.avg_completed_duration_s.toFixed(1)} s`
              : "—"
          }
          icon={Timer}
          hint={data.avg_completed_duration_s == null ? "no completed runs yet" : undefined}
        />
      </div>

      <div className="grid items-start gap-4 lg:grid-cols-3">
        {/* Recent runs */}
        <section className="rounded-xl border border-border bg-card shadow-[0_1px_3px_rgba(23,25,35,0.05)] lg:col-span-2">
          <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
            <h2 className="text-sm font-semibold">Recent runs</h2>
            <Link
              href="/runs"
              className="inline-flex items-center gap-0.5 text-xs font-medium text-[#635BFF] transition-colors duration-150 hover:text-[#4F48D6]"
            >
              View all <ArrowUpRight className="size-3" aria-hidden />
            </Link>
          </div>
          {data.recent_runs.length === 0 ? (
            <div className="p-4">
              <EmptyState
                title="Nothing has run yet"
                icon={Activity}
                hint="Submit your first task — the planner will decompose it into agent steps."
                action={
                  <Button size="sm" onClick={() => router.push("/workspace")}>
                    Open the workspace
                  </Button>
                }
              />
            </div>
          ) : (
            <Table>
              <TableBody>
                {data.recent_runs.map((run) => (
                  <TableRow
                    key={run.id}
                    className="cursor-pointer"
                    onClick={() => router.push(`/runs/${run.id}`)}
                  >
                    <TableCell className="max-w-0 w-full">
                      <p className="truncate text-sm">{run.title}</p>
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                      {formatTime(run.created_at)}
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={run.status} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </section>

        {/* Side column */}
        <div className="space-y-4">
          <section className="rounded-xl border border-border bg-card shadow-[0_1px_3px_rgba(23,25,35,0.05)]">
            <h2 className="border-b border-border px-4 py-2.5 text-sm font-semibold">
              Quick start
            </h2>
            <div className="space-y-1.5 p-3">
              {TEMPLATES.map((template) => (
                <button
                  key={template.title}
                  onClick={() => quickRun.mutate(template.prompt)}
                  disabled={quickRun.isPending}
                  className="w-full rounded-md border border-border px-3 py-2.5 text-left transition-colors duration-150 hover:border-[#C8CDFB] hover:bg-[#FAFAFF] disabled:opacity-60"
                >
                  <p className="text-[13px] font-medium">{template.title}</p>
                  <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
                    {template.prompt}
                  </p>
                </button>
              ))}
              <p className="px-1 pt-1 text-[11px] text-muted-foreground">
                Runs immediately with demo data seeded in this workspace.
              </p>
            </div>
          </section>

          <section className="rounded-xl border border-border bg-card shadow-[0_1px_3px_rgba(23,25,35,0.05)]">
            <h2 className="border-b border-border px-4 py-2.5 text-sm font-semibold">
              Activity
            </h2>
            <dl className="space-y-2 p-4 text-sm">
              <div className="flex items-center justify-between">
                <dt className="flex items-center gap-2 text-muted-foreground">
                  <Wrench className="size-3.5" aria-hidden /> Tool calls
                </dt>
                <dd className="font-mono tabular-nums">{data.tool_calls_total}</dd>
              </div>
              <div className="flex items-center justify-between">
                <dt className="text-muted-foreground">Documents</dt>
                <dd className="font-mono tabular-nums">{data.documents_total}</dd>
              </div>
              <div className="flex items-center justify-between">
                <dt className="text-muted-foreground">Tokens (in / out)</dt>
                <dd className="font-mono text-xs tabular-nums">
                  {data.tokens_input_total != null || data.tokens_output_total != null
                    ? `${data.tokens_input_total ?? 0} / ${data.tokens_output_total ?? 0}`
                    : "not reported"}
                </dd>
              </div>
              {Object.entries(data.runs_by_status).length > 0 && (
                <div className="border-t border-border pt-2">
                  {(
                    ["completed", "running", "waiting_approval", "queued", "failed", "cancelled"] as const
                  )
                    .filter((s) => (data.runs_by_status[s] ?? 0) > 0)
                    .map((status) => (
                      <div key={status} className="flex items-center justify-between py-1">
                        <StatusBadge status={status} />
                        <span className="font-mono text-sm tabular-nums">
                          {data.runs_by_status[status]}
                        </span>
                      </div>
                    ))}
                </div>
              )}
            </dl>
          </section>
        </div>
      </div>
    </div>
  );
}
