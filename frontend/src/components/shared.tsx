"use client";

import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  CircleDashed,
  Clock,
  Hourglass,
  Loader2,
  Pause,
  RotateCcw,
  ShieldQuestion,
  UserCheck,
  XCircle,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Skeleton } from "@/components/ui/skeleton";

/*
 * Status display: reserved semantic colors on soft tinted surfaces.
 * Always icon + label — color never carries state alone.
 */
const STATUS_STYLES: Record<
  string,
  { icon: React.ElementType; className: string; spin?: boolean }
> = {
  queued: { icon: Clock, className: "bg-secondary text-muted-foreground border-border" },
  pending: { icon: CircleDashed, className: "bg-secondary text-muted-foreground border-border" },
  planning: { icon: Loader2, className: "bg-blue-50 text-blue-700 border-blue-200", spin: true },
  running: { icon: Loader2, className: "bg-blue-50 text-blue-700 border-blue-200", spin: true },
  processing: { icon: Loader2, className: "bg-blue-50 text-blue-700 border-blue-200", spin: true },
  reviewing: { icon: Loader2, className: "bg-blue-50 text-blue-700 border-blue-200", spin: true },
  reporting: { icon: Loader2, className: "bg-blue-50 text-blue-700 border-blue-200", spin: true },
  waiting_approval: {
    icon: ShieldQuestion,
    className: "bg-amber-50 text-amber-800 border-amber-200",
  },
  pending_approval: {
    icon: ShieldQuestion,
    className: "bg-amber-50 text-amber-800 border-amber-200",
  },
  completed: { icon: CheckCircle2, className: "bg-emerald-50 text-emerald-700 border-emerald-200" },
  ready: { icon: CheckCircle2, className: "bg-emerald-50 text-emerald-700 border-emerald-200" },
  approved: { icon: CheckCircle2, className: "bg-emerald-50 text-emerald-700 border-emerald-200" },
  failed: { icon: XCircle, className: "bg-red-50 text-red-700 border-red-200" },
  rejected: { icon: XCircle, className: "bg-red-50 text-red-700 border-red-200" },
  timed_out: { icon: AlertTriangle, className: "bg-red-50 text-red-700 border-red-200" },
  expired: { icon: AlertTriangle, className: "bg-secondary text-muted-foreground border-border" },
  cancelling: { icon: Ban, className: "bg-secondary text-muted-foreground border-border" },
  cancelled: { icon: Ban, className: "bg-secondary text-muted-foreground border-border" },
  skipped: { icon: Ban, className: "bg-secondary text-muted-foreground border-border" },
  // PM harness task/project states
  draft: { icon: CircleDashed, className: "bg-secondary text-muted-foreground border-border" },
  planned: { icon: Clock, className: "bg-secondary text-muted-foreground border-border" },
  blocked: { icon: Hourglass, className: "bg-orange-50 text-orange-700 border-orange-200" },
  assigned: { icon: UserCheck, className: "bg-blue-50 text-blue-700 border-blue-200" },
  needs_revision: { icon: RotateCcw, className: "bg-amber-50 text-amber-800 border-amber-200" },
  succeeded: { icon: CheckCircle2, className: "bg-emerald-50 text-emerald-700 border-emerald-200" },
  active: { icon: Loader2, className: "bg-blue-50 text-blue-700 border-blue-200", spin: true },
  paused: { icon: Pause, className: "bg-secondary text-muted-foreground border-border" },
  awaiting_approval: {
    icon: ShieldQuestion,
    className: "bg-amber-50 text-amber-800 border-amber-200",
  },
};

export function StatusBadge({ status, className }: { status: string; className?: string }) {
  const style = STATUS_STYLES[status] ?? {
    icon: CircleDashed,
    className: "bg-secondary text-muted-foreground border-border",
  };
  const Icon = style.icon;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium",
        style.className,
        className,
      )}
    >
      <Icon className={cn("size-3.5", style.spin && "animate-spin")} aria-hidden />
      <span>{status.replace(/_/g, " ")}</span>
    </span>
  );
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: React.ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-[26px] font-semibold leading-8 tracking-[-0.01em]">{title}</h1>
        {description && (
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">{description}</p>
        )}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}

export function EmptyState({
  title,
  hint,
  action,
  icon: Icon,
}: {
  title: string;
  hint?: string;
  action?: React.ReactNode;
  icon?: React.ElementType;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-border bg-card py-14 text-center">
      {Icon && (
        <div className="mb-3 flex size-10 items-center justify-center rounded-lg bg-secondary">
          <Icon className="size-5 text-muted-foreground" aria-hidden />
        </div>
      )}
      <p className="text-sm font-medium">{title}</p>
      {hint && <p className="mt-1 max-w-sm text-sm text-muted-foreground">{hint}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorState({ message, retry }: { message: string; retry?: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-red-200 bg-red-50 py-10 text-center">
      <AlertTriangle className="mb-2 size-5 text-red-600" aria-hidden />
      <p className="text-sm text-red-700">{message}</p>
      {retry && (
        <button
          onClick={retry}
          className="mt-3 text-sm font-medium text-red-700 underline underline-offset-4 transition-colors duration-150 hover:text-red-800"
        >
          Try again
        </button>
      )}
    </div>
  );
}

export function LoadingRows({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-2" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-11 w-full rounded-lg bg-secondary" />
      ))}
    </div>
  );
}

export function StatTile({
  label,
  value,
  hint,
  icon: Icon,
}: {
  label: string;
  value: string | number;
  hint?: string;
  icon?: React.ElementType;
}) {
  return (
    <div className="rounded-lg border border-border bg-card px-4 py-3.5">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[13px] font-medium text-muted-foreground">{label}</p>
        {Icon && <Icon className="size-4 text-muted-foreground/70" aria-hidden />}
      </div>
      <p className="mt-1.5 text-2xl font-semibold tabular-nums tracking-tight">{value}</p>
      {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

export function formatDuration(startIso: string | null, endIso: string | null): string {
  if (!startIso || !endIso) return "—";
  const ms = new Date(endIso).getTime() - new Date(startIso).getTime();
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`;
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatClock(iso: string | null | undefined): string {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}
