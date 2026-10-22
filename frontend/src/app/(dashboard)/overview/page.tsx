"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  Activity,
  ArrowUpRight,
  CheckCircle2,
  ListTodo,
  Plus,
  Timer,
  Wrench,
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
          <h1 className="text-[26px] font-semibold leading-8 tracking-[-0.01em]">
            {me?.memberships[0]?.organization.name ?? "Workspace"}
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Operational view of your agent runs — every figure comes from persisted execution
            data.
          </p>
        </div>
        <Button onClick={() => router.push("/workspace")}>
          <Plus className="size-4" aria-hidden /> New task
        </Button>
      </div>

      {/* Metrics */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Total runs" value={data.runs_total} icon={ListTodo} />
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
        <section className="rounded-lg border border-border bg-card lg:col-span-2">
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
          <section className="rounded-lg border border-border bg-card">
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

          <section className="rounded-lg border border-border bg-card">
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
