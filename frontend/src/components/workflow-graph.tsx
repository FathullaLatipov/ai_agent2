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
  ClipboardList,
  FileText,
  Loader2,
  Search,
  ShieldCheck,
  ShieldQuestion,
  XCircle,
  Zap,
} from "lucide-react";
import type { RunDetail } from "@/lib/types";
import { cn } from "@/lib/utils";

const NODE_W = 196;
const NODE_H = 62;
const GAP_X = 64;
const GAP_Y = 22;

// Status → accent. Pending gray · running blue · completed green · failed red ·
// approval amber. Each node also shows an explicit status icon + label.
const STATUS_ACCENT: Record<string, { border: string; icon: React.ElementType; iconClass: string; spin?: boolean }> = {
  pending: { border: "border-border", icon: CircleDashed, iconClass: "text-muted-foreground" },
  running: { border: "border-blue-400", icon: Loader2, iconClass: "text-blue-600", spin: true },
  waiting_approval: {
    border: "border-amber-400",
    icon: ShieldQuestion,
    iconClass: "text-amber-600",
  },
  completed: { border: "border-emerald-400", icon: CheckCircle2, iconClass: "text-emerald-600" },
  failed: { border: "border-red-400", icon: XCircle, iconClass: "text-red-600" },
  cancelled: { border: "border-border", icon: CircleDashed, iconClass: "text-muted-foreground" },
  skipped: { border: "border-border", icon: CircleDashed, iconClass: "text-muted-foreground" },
};

const AGENT_ICONS: Record<string, React.ElementType> = {
  planner: ClipboardList,
  research: Search,
  analyst: BarChart3,
  executor: Zap,
  reviewer: ShieldCheck,
  reporter: FileText,
};

type StageData = { label: string; sub: string; status: string; agent: string };

function StageNode({ data }: NodeProps) {
  const d = data as StageData;
  const accent = STATUS_ACCENT[d.status] ?? STATUS_ACCENT.pending;
  const AgentIcon = AGENT_ICONS[d.agent] ?? Bot;
  const StatusIcon = accent.icon;
  return (
    <div
      className={cn(
        "h-[62px] w-[196px] rounded-lg border bg-white px-3 py-2 shadow-[0_1px_2px_rgba(16,24,40,0.05)]",
        accent.border,
        d.status === "pending" ? "border" : "border-[1.5px]",
      )}
    >
      <Handle type="target" position={Position.Left} className="opacity-0" />
      <div className="flex items-center gap-1.5">
        <AgentIcon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
        <p className="truncate text-xs font-semibold text-foreground">{d.label}</p>
      </div>
      <p className="mt-0.5 truncate text-[11px] text-muted-foreground">{d.sub}</p>
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
    </div>
  );
}

const nodeTypes = { stage: StageNode };

const EDGE_STYLE = { stroke: "#CBD2DC", strokeWidth: 1.25 };
const EDGE_STYLE_ACTIVE = { stroke: "#3B82F6", strokeWidth: 1.5 };

/** Dependency-layered layout: planner → tasks (by dep depth) → review → report. */
export function WorkflowGraph({ run }: { run: RunDetail }) {
  const { nodes, edges } = useMemo(() => buildGraph(run), [run]);
  return (
    <div className="h-72 w-full overflow-hidden rounded-lg border border-border bg-[#FBFBFD]">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.18 }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        panOnDrag
        zoomOnScroll={false}
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={18} size={1} color="#E2E5EA" />
      </ReactFlow>
    </div>
  );
}

function buildGraph(run: RunDetail): { nodes: Node[]; edges: Edge[] } {
  const tasks = run.tasks ?? [];
  const depth = new Map<number, number>();
  for (const task of [...tasks].sort((a, b) => a.ordinal - b.ordinal)) {
    const d = task.depends_on.length
      ? Math.max(...task.depends_on.map((o) => depth.get(o) ?? 0)) + 1
      : 0;
    depth.set(task.ordinal, d);
  }
  const maxDepth = Math.max(0, ...depth.values());
  const layerCounts = new Map<number, number>();

  const planStatus =
    run.plan != null ? "completed" : run.status === "failed" ? "failed" : "running";
  const nodes: Node[] = [
    {
      id: "plan",
      type: "stage",
      position: { x: 0, y: 0 },
      data: { label: "Planner", sub: "execution plan", status: planStatus, agent: "planner" },
    },
  ];
  const edges: Edge[] = [];

  const edgeFor = (id: string, source: string, target: string, animated: boolean): Edge => ({
    id,
    source,
    target,
    animated,
    style: animated ? EDGE_STYLE_ACTIVE : EDGE_STYLE,
  });

  for (const task of tasks) {
    const d = depth.get(task.ordinal) ?? 0;
    const idx = layerCounts.get(d) ?? 0;
    layerCounts.set(d, idx + 1);
    nodes.push({
      id: `t${task.ordinal}`,
      type: "stage",
      position: { x: (d + 1) * (NODE_W + GAP_X), y: idx * (NODE_H + GAP_Y) },
      data: {
        label: `${task.ordinal}. ${task.agent_key}`,
        sub: task.objective,
        status: task.status,
        agent: task.agent_key,
      },
    });
    if (task.depends_on.length === 0) {
      edges.push(
        edgeFor(`e-plan-t${task.ordinal}`, "plan", `t${task.ordinal}`, task.status === "running"),
      );
    }
    for (const dep of task.depends_on) {
      edges.push(
        edgeFor(
          `e-t${dep}-t${task.ordinal}`,
          `t${dep}`,
          `t${task.ordinal}`,
          task.status === "running",
        ),
      );
    }
  }

  const reviewDone = ["reporting", "completed"].includes(run.status);
  const reviewStatus =
    run.status === "reviewing" ? "running" : reviewDone ? "completed" : "pending";
  const reportStatus =
    run.status === "reporting" ? "running" : run.status === "completed" ? "completed" : "pending";
  const reviewX = (maxDepth + 2) * (NODE_W + GAP_X);
  nodes.push(
    {
      id: "review",
      type: "stage",
      position: { x: reviewX, y: 0 },
      data: { label: "Reviewer", sub: "validation", status: reviewStatus, agent: "reviewer" },
    },
    {
      id: "report",
      type: "stage",
      position: { x: reviewX + NODE_W + GAP_X, y: 0 },
      data: { label: "Reporter", sub: "final report", status: reportStatus, agent: "reporter" },
    },
  );
  const dependedOn = new Set(tasks.flatMap((t) => t.depends_on));
  for (const task of tasks) {
    if (!dependedOn.has(task.ordinal)) {
      edges.push(edgeFor(`e-t${task.ordinal}-review`, `t${task.ordinal}`, "review", false));
    }
  }
  if (tasks.length === 0) edges.push(edgeFor("e-plan-review", "plan", "review", false));
  edges.push(
    edgeFor("e-review-report", "review", "report", reviewStatus === "running"),
  );
  return { nodes, edges };
}
