"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  BarChart3,
  Bot,
  ChevronDown,
  ClipboardList,
  FileText,
  Search,
  ShieldCheck,
  Zap,
} from "lucide-react";
import { api } from "@/lib/api";
import type { AgentDefinition, Capabilities } from "@/lib/types";
import { EmptyState, ErrorState, LoadingRows, PageHeader } from "@/components/shared";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const AGENT_ICONS: Record<string, React.ElementType> = {
  planner: ClipboardList,
  research: Search,
  analyst: BarChart3,
  executor: Zap,
  reviewer: ShieldCheck,
  reporter: FileText,
};

export default function AgentsPage() {
  const agents = useQuery({
    queryKey: ["agents"],
    queryFn: () => api<AgentDefinition[]>("/agents"),
  });
  const capabilities = useQuery({
    queryKey: ["capabilities"],
    queryFn: () => api<Capabilities>("/meta/capabilities"),
  });

  // llm is null for non-admins — show a neutral label instead of provider details.
  const modelLabel = !capabilities.data
    ? "…"
    : capabilities.data.llm == null
      ? "managed by the platform"
      : capabilities.data.llm.configured
        ? capabilities.data.llm.model
        : "deterministic (no LLM configured)";

  return (
    <div>
      <PageHeader
        title="Agents"
        description="The specialized agents that execute your tasks. Tool allowlists and execution limits are enforced server-side on every call."
      />
      {agents.isLoading ? (
        <LoadingRows />
      ) : agents.isError ? (
        <ErrorState message={agents.error.message} retry={() => agents.refetch()} />
      ) : agents.data!.length === 0 ? (
        <EmptyState
          title="No agents registered"
          icon={Bot}
          hint="Run the seed script or restart the API to sync the system agents."
        />
      ) : (
        <div className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-card">
          {agents.data!.map((agent) => (
            <AgentRow key={agent.id} agent={agent} modelLabel={modelLabel ?? "—"} />
          ))}
        </div>
      )}
    </div>
  );
}

function AgentRow({ agent, modelLabel }: { agent: AgentDefinition; modelLabel: string }) {
  const [open, setOpen] = useState(false);
  const Icon = AGENT_ICONS[agent.key] ?? Bot;
  return (
    <div>
      <button
        className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors duration-150 hover:bg-secondary/50"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-[#EEF0FE]">
          <Icon className="size-4 text-[#4F48D6]" aria-hidden />
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium">{agent.name}</span>
            <span className="font-mono text-xs text-muted-foreground">{agent.key}</span>
            {agent.is_system && (
              <Badge variant="outline" className="h-5 text-[10px]">
                system
              </Badge>
            )}
            {!agent.enabled && (
              <Badge variant="outline" className="h-5 border-red-200 text-[10px] text-red-600">
                disabled
              </Badge>
            )}
          </span>
          <span className="mt-0.5 line-clamp-1 block text-xs text-muted-foreground">
            {agent.description}
          </span>
        </span>
        <span className="hidden shrink-0 text-xs text-muted-foreground sm:block">
          {agent.allowed_tools.length} tool{agent.allowed_tools.length === 1 ? "" : "s"}
        </span>
        <ChevronDown
          className={cn(
            "size-4 shrink-0 text-muted-foreground transition-transform duration-150",
            open && "rotate-180",
          )}
          aria-hidden
        />
      </button>
      {open && (
        <div className="space-y-3 border-t border-border bg-[#FBFBFD] px-4 py-3.5 pl-[60px]">
          <p className="text-sm text-muted-foreground">{agent.description}</p>
          <div>
            <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
              Tool permissions
            </p>
            {agent.allowed_tools.length === 0 ? (
              <p className="text-xs text-muted-foreground">none — pure planning/validation</p>
            ) : (
              <div className="flex flex-wrap gap-1">
                {agent.allowed_tools.map((tool) => (
                  <span
                    key={tool}
                    className="rounded-md border border-border bg-card px-2 py-0.5 font-mono text-[11px] text-foreground/80"
                  >
                    {tool}
                  </span>
                ))}
              </div>
            )}
          </div>
          <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-muted-foreground">
            <span>
              model: <span className="font-mono">{agent.model ?? modelLabel}</span>
            </span>
            <span>max iterations: {agent.max_iterations}</span>
            <span>timeout: {agent.timeout_s}s</span>
          </div>
        </div>
      )}
    </div>
  );
}
