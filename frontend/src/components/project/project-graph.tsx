"use client";

import { useMemo } from "react";
import {
  Background,
  BackgroundVariant,
  Handle,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  BarChart3,
  Bot,
  CheckCircle2,
  CircleDashed,
  Hourglass,
  Loader2,
  Search,
  ShieldCheck,
  ShieldQuestion,
  XCircle,
  Zap,
} from "lucide-react";
import type { ProjectGraph } from "@/lib/types";
import { cn } from "@/lib/utils";

const NODE_W = 208;
const NODE_H = 64;
const GAP_X = 70;
const GAP_Y = 26;

const STATUS_ACCENT: Record<
  string,
  { border: string; icon: React.ElementType; iconClass: string; spin?: boolean }
> = {
  draft: { border: "border-border", icon: CircleDashed, iconClass: "text-muted-foreground" },
  planned: { border: "border-border", icon: CircleDashed, iconClass: "text-muted-foreground" },
  blocked: { border: "border-orange-300", icon: Hourglass, iconClass: "text-orange-600" },
  ready: { border: "border-blue-300", icon: CircleDashed, iconClass: "text-blue-600" },
  waiting_approval: {
    border: "border-amber-400",
    icon: ShieldQuestion,
    iconClass: "text-amber-600",
  },
  assigned: { border: "border-blue-400", icon: Loader2, iconClass: "text-blue-600", spin: true },
  running: { border: "border-blue-400", icon: Loader2, iconClass: "text-blue-600", spin: true },
  reviewing: { border: "border-blue-400", icon: Loader2, iconClass: "text-blue-600", spin: true },
  needs_revision: {
    border: "border-amber-400",
    icon: ShieldQuestion,
    iconClass: "text-amber-600",
  },
  succeeded: { border: "border-emerald-400", icon: CheckCircle2, iconClass: "text-emerald-600" },
  failed: { border: "border-red-400", icon: XCircle, iconClass: "text-red-600" },
  cancelled: { border: "border-border", icon: XCircle, iconClass: "text-muted-foreground" },
};

const AGENT_ICONS: Record<string, React.ElementType> = {
  research: Search,
  analyst: BarChart3,
  executor: Zap,
  reviewer: ShieldCheck,
};

type NodeData = {
  ordinal: number;
  title: string;
  agent: string | null;
  status: string;
  blocked_reason: string | null;
  onSelect: (ordinal: number) => void;
};

function TaskNode({ data }: NodeProps) {
  const d = data as NodeData;
  const accent = STATUS_ACCENT[d.status] ?? STATUS_ACCENT.draft;
  const AgentIcon = d.agent ? (AGENT_ICONS[d.agent] ?? Bot) : Bot;
  const StatusIcon = accent.icon;
  return (
    <button
      onClick={() => d.onSelect(d.ordinal)}
      className={cn(
        "h-[64px] w-[208px] rounded-lg border bg-white px-3 py-2 text-left shadow-[0_1px_2px_rgba(16,24,40,0.05)] transition-shadow duration-150 hover:shadow-[0_2px_6px_rgba(16,24,40,0.1)]",
        accent.border,
        d.status === "draft" || d.status === "planned" ? "border" : "border-[1.5px]",
      )}
      title={d.blocked_reason ?? undefined}
    >
      <Handle type="target" position={Position.Left} className="opacity-0" />
      <div className="flex items-center gap-1.5">
        <span className="font-mono text-[10px] text-muted-foreground">#{d.ordinal}</span>
        <p className="truncate text-xs font-semibold text-foreground">{d.title}</p>
      </div>
      <div className="mt-0.5 flex items-center gap-1 text-[11px] text-muted-foreground">
        <AgentIcon className="size-3" aria-hidden />
        {d.agent ?? "unassigned"}
      </div>
      <div className="mt-0.5 flex items-center gap-1">
        <StatusIcon
          className={cn("size-3", accent.iconClass, accent.spin && "animate-spin")}
          aria-hidden
        />
        <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
          {d.status.replace(/_/g, " ")}
        </p>
      </div>
      <Handle type="source" position={Position.Right} className="opacity-0" />
    </button>
  );
}

const nodeTypes = { task: TaskNode };

export function ProjectGraphView({
  graph,
  onSelect,
}: {
  graph: ProjectGraph;
  onSelect: (ordinal: number) => void;
}) {
  const { nodes, edges } = useMemo(() => {
    const incoming = new Map<string, string[]>();
    for (const edge of graph.edges) {
      incoming.set(edge.target, [...(incoming.get(edge.target) ?? []), edge.source]);
    }
    const depth = new Map<string, number>();
    const resolve = (id: string, seen: Set<string>): number => {
      if (depth.has(id)) return depth.get(id)!;
      if (seen.has(id)) return 0; // cycles are rejected server-side; guard anyway
      seen.add(id);
      const sources = incoming.get(id) ?? [];
      const d = sources.length
        ? Math.max(...sources.map((s) => resolve(s, seen))) + 1
        : 0;
      depth.set(id, d);
      return d;
    };
    graph.nodes.forEach((n) => resolve(n.id, new Set()));
    const layerCounts = new Map<number, number>();
    const nodes: Node[] = graph.nodes.map((n) => {
      const d = depth.get(n.id) ?? 0;
      const idx = layerCounts.get(d) ?? 0;
      layerCounts.set(d, idx + 1);
      return {
        id: n.id,
        type: "task",
        position: { x: d * (NODE_W + GAP_X), y: idx * (NODE_H + GAP_Y) },
        data: {
          ordinal: n.ordinal,
          title: n.title,
          agent: n.agent_key,
          status: n.status,
          blocked_reason: n.blocked_reason,
          onSelect,
        },
      };
    });
    const statusById = new Map(graph.nodes.map((n) => [n.id, n.status]));
    const edges: Edge[] = graph.edges.map((e) => ({
      id: `${e.source}-${e.target}`,
      source: e.source,
      target: e.target,
      animated: statusById.get(e.target) === "running",
      style:
        statusById.get(e.target) === "running"
          ? { stroke: "#3B82F6", strokeWidth: 1.5 }
          : { stroke: "#CBD2DC", strokeWidth: 1.25 },
    }));
    return { nodes, edges };
  }, [graph, onSelect]);

  if (graph.nodes.length === 0) {
    return (
      <div className="flex h-72 items-center justify-center rounded-lg border border-dashed border-border bg-card text-sm text-muted-foreground">
        No tasks yet — approve a plan or add tasks to see the workflow graph.
      </div>
    );
  }

  return (
    <div className="h-[22rem] w-full overflow-hidden rounded-lg border border-border bg-[#FBFBFD]">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.18 }}
        nodesDraggable={false}
        nodesConnectable={false}
        panOnDrag
        zoomOnScroll={false}
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={18} size={1} color="#E2E5EA" />
      </ReactFlow>
    </div>
  );
}
