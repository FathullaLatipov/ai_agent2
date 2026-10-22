"use client";

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, RotateCcw, XCircle } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { PMTaskDetail } from "@/lib/types";
import { formatTime, LoadingRows, StatusBadge } from "@/components/shared";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select";
import { useState } from "react";

const ASSIGNABLE = ["research", "analyst", "executor", "reviewer"];

export function TaskDetailsDialog({
  projectId,
  taskId,
  onClose,
}: {
  projectId: string;
  taskId: string | null;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [agent, setAgent] = useState<string>("");

  const task = useQuery({
    queryKey: ["project-task", projectId, taskId],
    queryFn: () => api<PMTaskDetail>(`/projects/${projectId}/tasks/${taskId}`),
    enabled: Boolean(taskId),
  });

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    void queryClient.invalidateQueries({ queryKey: ["project-graph", projectId] });
    void queryClient.invalidateQueries({ queryKey: ["project-task", projectId, taskId] });
  };

  const act = useMutation({
    mutationFn: ({ action, body }: { action: string; body?: object }) =>
      api<{ status: string; message: string }>(
        `/projects/${projectId}/tasks/${taskId}/${action}`,
        { method: "POST", body: body ? JSON.stringify(body) : undefined },
      ),
    onSuccess: (result) => {
      toast[result.status === "rejected" ? "error" : "success"](result.message);
      refresh();
    },
    onError: (e) => toast.error(e.message),
  });

  const detail = task.data;

  return (
    <Dialog open={Boolean(taskId)} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        {task.isLoading || !detail ? (
          <LoadingRows rows={4} />
        ) : (
          <>
            <DialogHeader>
              <DialogTitle className="flex flex-wrap items-center gap-2 pr-6 text-base">
                <span className="font-mono text-sm text-muted-foreground">
                  #{detail.ordinal}
                </span>
                {detail.title}
                <StatusBadge status={detail.status} />
              </DialogTitle>
            </DialogHeader>

            <div className="space-y-4 text-sm">
              {detail.description && (
                <p className="text-muted-foreground">{detail.description}</p>
              )}

              {detail.blocked_reason && (
                <p className="rounded-md border border-orange-200 bg-orange-50 px-3 py-2 text-xs text-orange-800">
                  Blocked: {detail.blocked_reason}
                </p>
              )}
              {detail.error && (
                <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
                  {detail.error}
                </p>
              )}
              {detail.revision_feedback && (
                <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                  Rework feedback: {detail.revision_feedback}
                </p>
              )}

              <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-[13px]">
                <dt className="text-muted-foreground">Agent</dt>
                <dd>
                  {detail.agent_key ?? "unassigned"}
                  {detail.requested_agent_key &&
                    detail.requested_agent_key !== detail.agent_key && (
                      <span className="text-muted-foreground">
                        {" "}
                        (owner requested {detail.requested_agent_key})
                      </span>
                    )}
                </dd>
                <dt className="text-muted-foreground">Attempts</dt>
                <dd className="font-mono tabular-nums">
                  {detail.attempts} / {detail.max_attempts}
                </dd>
                <dt className="text-muted-foreground">Depends on</dt>
                <dd>
                  {detail.depends_on.length
                    ? detail.depends_on.map((d) => `#${d}`).join(", ")
                    : "—"}
                </dd>
                <dt className="text-muted-foreground">Unblocks</dt>
                <dd>
                  {detail.dependents.length
                    ? detail.dependents.map((d) => `#${d}`).join(", ")
                    : "—"}
                </dd>
                <dt className="text-muted-foreground">Created</dt>
                <dd>{formatTime(detail.created_at)}</dd>
                <dt className="text-muted-foreground">Finished</dt>
                <dd>{formatTime(detail.finished_at)}</dd>
              </dl>

              {detail.acceptance_criteria.length > 0 && (
                <div>
                  <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    Acceptance criteria
                  </p>
                  <ul className="list-disc space-y-0.5 pl-5 text-[13px]">
                    {detail.acceptance_criteria.map((criterion, i) => (
                      <li key={i}>{criterion.description}</li>
                    ))}
                  </ul>
                </div>
              )}

              {detail.result?.artifacts && detail.result.artifacts.length > 0 && (
                <div>
                  <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    Artifacts
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {detail.result.artifacts.map((artifact, i) => (
                      <span
                        key={i}
                        className="rounded-md border border-border bg-secondary/60 px-2 py-0.5 text-xs"
                      >
                        {artifact.label}{" "}
                        <span className="text-muted-foreground">({artifact.kind})</span>
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {detail.reviews.length > 0 && (
                <div>
                  <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    Acceptance reviews
                  </p>
                  <div className="space-y-1.5">
                    {detail.reviews.map((review) => (
                      <div
                        key={review.attempt}
                        className="rounded-md border border-border px-2.5 py-1.5 text-xs"
                      >
                        <p className="font-medium">
                          Attempt {review.attempt}:{" "}
                          <span className={review.passed ? "text-emerald-700" : "text-red-600"}>
                            {review.passed ? "PASS" : "FAIL"}
                          </span>{" "}
                          <span className="text-muted-foreground">({review.reviewer})</span>
                        </p>
                        {review.issues.length > 0 && (
                          <ul className="mt-0.5 list-disc pl-4 text-muted-foreground">
                            {review.issues.map((issue, i) => (
                              <li key={i}>{String(issue)}</li>
                            ))}
                          </ul>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {detail.run_id && (
                <Link
                  href={`/runs/${detail.run_id}`}
                  className="inline-flex items-center gap-1 text-xs font-medium text-[#635BFF] hover:text-[#4F48D6]"
                >
                  Open execution run <ExternalLink className="size-3" aria-hidden />
                </Link>
              )}

              {/* Actions — enabled only when the backend supports them */}
              <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
                <Select value={agent || "pick"} onValueChange={(v) => setAgent(v === "pick" ? "" : (v ?? ""))}>
                  <SelectTrigger className="h-8 w-40 text-xs" aria-label="Agent">
                    <span>{agent || "Choose agent…"}</span>
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="pick">Choose agent…</SelectItem>
                    {ASSIGNABLE.map((key) => (
                      <SelectItem key={key} value={key}>
                        {key}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={!agent || act.isPending}
                  onClick={() => act.mutate({ action: "assign", body: { agent_key: agent, mode: "assign" } })}
                >
                  Assign
                </Button>
                <Button
                  size="sm"
                  disabled={!agent || act.isPending}
                  onClick={() =>
                    act.mutate({ action: "assign", body: { agent_key: agent, mode: "start_now" } })
                  }
                >
                  Start now
                </Button>
                {detail.status === "failed" && (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={act.isPending}
                    onClick={() => act.mutate({ action: "retry" })}
                  >
                    <RotateCcw className="size-3.5" aria-hidden /> Retry
                  </Button>
                )}
                {!["succeeded", "cancelled", "failed"].includes(detail.status) && (
                  <Button
                    size="sm"
                    variant="ghost"
                    className="text-muted-foreground"
                    disabled={act.isPending}
                    onClick={() => act.mutate({ action: "cancel" })}
                  >
                    <XCircle className="size-3.5" aria-hidden /> Cancel
                  </Button>
                )}
              </div>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
