"use client";

import { useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, ShieldQuestion } from "lucide-react";
import { toast } from "sonner";
import { api, useAuth } from "@/lib/api";
import type { Approval } from "@/lib/types";
import {
  EmptyState,
  ErrorState,
  formatTime,
  LoadingRows,
  PageHeader,
  StatusBadge,
} from "@/components/shared";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";

export default function ApprovalsPage() {
  const [tab, setTab] = useState<"pending" | "all">("pending");
  const queryClient = useQueryClient();
  const role = useAuth((s) => s.me?.memberships[0]?.role);
  const canDecide = role === "owner" || role === "admin";
  const [reasons, setReasons] = useState<Record<string, string>>({});

  const approvals = useQuery({
    queryKey: ["approvals", tab],
    queryFn: () => api<Approval[]>(`/approvals${tab === "pending" ? "?status=pending" : ""}`),
    refetchInterval: 10_000,
  });

  const decide = useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: "approve" | "reject" }) =>
      api(`/approvals/${id}/decide`, {
        method: "POST",
        body: JSON.stringify({ decision, reason: reasons[id] || undefined }),
      }),
    onSuccess: (_d, vars) => {
      toast.success(`Operation ${vars.decision}d — the run resumes automatically`);
      void queryClient.invalidateQueries({ queryKey: ["approvals"] });
    },
    onError: (e) => toast.error(e.message),
  });

  return (
    <div>
      <PageHeader
        title="Approvals"
        description="High-impact operations pause here until a human decides. Decisions are server-side state transitions, recorded in the audit log."
      />
      <Tabs value={tab} onValueChange={(v) => setTab(v as typeof tab)} className="mb-4">
        <TabsList>
          <TabsTrigger value="pending">Pending</TabsTrigger>
          <TabsTrigger value="all">All</TabsTrigger>
        </TabsList>
      </Tabs>

      {!canDecide && (
        <p className="mb-4 rounded-lg border border-border bg-card px-3.5 py-2.5 text-sm text-muted-foreground">
          Your role ({role}) can view approvals; only admins and owners can decide them.
        </p>
      )}

      {approvals.isLoading ? (
        <LoadingRows />
      ) : approvals.isError ? (
        <ErrorState message={approvals.error.message} retry={() => approvals.refetch()} />
      ) : approvals.data!.length === 0 ? (
        <EmptyState
          title={tab === "pending" ? "No pending approvals" : "No approval requests yet"}
          icon={ShieldQuestion}
          hint="Requests appear when an agent attempts a high-impact operation, such as sending a CRM follow-up."
        />
      ) : (
        <div className="space-y-3">
          {approvals.data!.map((approval) => (
            <section
              key={approval.id}
              className="rounded-lg border border-border bg-card p-4"
            >
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-sm font-medium">{approval.tool_name}</span>
                    <StatusBadge status={approval.status} />
                    <span className="text-xs text-muted-foreground">
                      requested by agent “{approval.requested_by_agent}” ·{" "}
                      {formatTime(approval.created_at)}
                    </span>
                  </div>
                  <p className="mt-1 text-sm text-muted-foreground">
                    {approval.operation_summary}
                  </p>
                  {approval.payload.arguments && (
                    <pre className="mt-2 max-h-48 max-w-2xl overflow-auto rounded-md bg-secondary/70 p-2.5 font-mono text-[11px] text-foreground/80">
                      {JSON.stringify(approval.payload.arguments, null, 2)}
                    </pre>
                  )}
                  <div className="mt-2 flex flex-wrap items-center gap-3">
                    <Link
                      href={`/runs/${approval.run_id}`}
                      className="inline-flex items-center gap-1 text-xs font-medium text-[#635BFF] transition-colors duration-150 hover:text-[#4F48D6]"
                    >
                      Open run <ExternalLink className="size-3" aria-hidden />
                    </Link>
                    {approval.status === "pending" ? (
                      <span className="text-xs text-muted-foreground">
                        Approving executes the operation immediately; rejecting fails the task.
                        Expires {formatTime(approval.expires_at)}.
                      </span>
                    ) : (
                      <span className="text-xs text-muted-foreground">
                        decided {formatTime(approval.decided_at)}
                        {approval.decision_reason ? ` — “${approval.decision_reason}”` : ""}
                      </span>
                    )}
                  </div>
                </div>
                {approval.status === "pending" && canDecide && (
                  <div className="w-full max-w-64 shrink-0 space-y-2 sm:w-64">
                    <Textarea
                      rows={2}
                      placeholder="Reason (optional, audited)"
                      className="bg-card text-sm"
                      value={reasons[approval.id] ?? ""}
                      onChange={(e) =>
                        setReasons((r) => ({ ...r, [approval.id]: e.target.value }))
                      }
                    />
                    <div className="flex gap-2">
                      <Button
                        size="sm"
                        className="flex-1"
                        disabled={decide.isPending}
                        onClick={() => decide.mutate({ id: approval.id, decision: "approve" })}
                      >
                        Approve
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        className="flex-1"
                        disabled={decide.isPending}
                        onClick={() => decide.mutate({ id: approval.id, decision: "reject" })}
                      >
                        Reject
                      </Button>
                    </div>
                  </div>
                )}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
