// API types mirroring backend/app/schemas.

export type RunStatus =
  | "queued"
  | "planning"
  | "running"
  | "waiting_approval"
  | "reviewing"
  | "reporting"
  | "completed"
  | "failed"
  | "cancelling"
  | "cancelled";

export type TaskStatus =
  | "pending"
  | "running"
  | "waiting_approval"
  | "completed"
  | "failed"
  | "skipped"
  | "cancelled";

export type ExecutionMode = "llm" | "deterministic";

export interface TokenResponse {
  access_token: string;
}

export interface Membership {
  organization: { id: string; name: string; slug: string };
  role: "owner" | "admin" | "member" | "viewer";
}

export interface Me {
  user: { id: string; email: string; full_name: string };
  memberships: Membership[];
}

export interface RunSummary {
  id: string;
  title: string;
  status: RunStatus;
  mode: ExecutionMode;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  tokens_input: number | null;
  tokens_output: number | null;
  llm_calls: number;
  agents?: string[];
}

export interface RunTask {
  id: string;
  ordinal: number;
  agent_key: string;
  objective: string;
  depends_on: number[];
  status: TaskStatus;
  attempts: number;
  output: Record<string, unknown> | null;
  error: string | null;
  mode: ExecutionMode;
  started_at: string | null;
  finished_at: string | null;
}

export interface ToolCall {
  id: string;
  tool_name: string;
  agent_key: string;
  arguments: Record<string, unknown>;
  result_summary: Record<string, unknown> | null;
  status: string;
  error: string | null;
  duration_ms: number | null;
  created_at: string;
  finished_at: string | null;
}

export interface Approval {
  id: string;
  run_id: string;
  tool_name: string;
  operation_summary: string;
  payload: { arguments?: Record<string, unknown> };
  status: "pending" | "approved" | "rejected" | "expired";
  requested_by_agent: string;
  decided_by: string | null;
  decided_at: string | null;
  decision_reason: string | null;
  expires_at: string;
  created_at: string;
}

export interface Report {
  id: string;
  title: string;
  format: string;
  body_markdown: string;
  created_at: string;
}

export interface RunDetail extends RunSummary {
  input_text: string;
  plan: { goal: string; tasks: PlanTask[]; notes?: string } | null;
  budgets: Record<string, unknown>;
  tasks: RunTask[];
  tool_calls: ToolCall[];
  approvals: Approval[];
  report: Report | null;
}

export interface PlanTask {
  ordinal: number;
  agent: string;
  objective: string;
  depends_on: number[];
  params: Record<string, unknown>;
}

export interface RunEvent {
  seq: number;
  type: string;
  payload: Record<string, unknown>;
  created_at?: string;
}

export interface AgentDefinition {
  id: string;
  key: string;
  name: string;
  description: string;
  model: string | null;
  allowed_tools: string[];
  max_iterations: number;
  timeout_s: number;
  enabled: boolean;
  is_system: boolean;
}

export interface ToolInfo {
  name: string;
  description: string;
  source: string;
  requires_approval: boolean;
  required_permission: string;
  timeout_s: number;
  idempotent: boolean;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
}

export interface McpServer {
  name: string;
  command: string;
  enabled: boolean;
  status: string;
  tools: { name: string; description: string }[];
}

export interface DocumentInfo {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  status: "pending" | "processing" | "ready" | "failed";
  error: string | null;
  chunk_count: number;
  embeddings_provider: string | null;
  created_at: string;
  updated_at: string;
}

export interface SearchHit {
  document_id: string;
  chunk_id: string;
  filename: string;
  ordinal: number;
  score: number;
  excerpt: string;
}

export interface Capabilities {
  /** null when the caller is not an admin/owner — provider details are hidden. */
  llm: {
    provider: string;
    configured: boolean;
    model: string | null;
    note: string | null;
  } | null;
  embeddings: { provider: string; semantic: boolean; note: string | null };
  vector_search: { pgvector: boolean; path: string };
  limits: Record<string, number>;
  environment: string;
}

// ---------- PM harness ----------

export type PMTaskStatus =
  | "draft"
  | "planned"
  | "blocked"
  | "ready"
  | "waiting_approval"
  | "assigned"
  | "running"
  | "reviewing"
  | "needs_revision"
  | "succeeded"
  | "failed"
  | "cancelled";

export type ProjectStatus =
  | "draft"
  | "planning"
  | "awaiting_approval"
  | "active"
  | "paused"
  | "completed"
  | "failed"
  | "cancelled";

export interface ProjectSummary {
  id: string;
  name: string;
  goal: string;
  status: ProjectStatus;
  created_at: string;
  updated_at: string;
}

export interface PMTask {
  id: string;
  ordinal: number;
  title: string;
  description: string;
  status: PMTaskStatus;
  priority: number;
  agent_key: string | null;
  requested_agent_key: string | null;
  blocked_reason: string | null;
  attempts: number;
  max_attempts: number;
  run_id: string | null;
  error: string | null;
  revision_feedback: string | null;
  acceptance_criteria: { description: string; kind: string }[];
  expected_deliverables: string[];
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  depends_on: number[];
}

export interface PMTaskDetail extends PMTask {
  params: Record<string, unknown>;
  result: { artifacts?: { kind: string; label: string }[] } | null;
  dependents: number[];
  reviews: {
    attempt: number;
    passed: boolean;
    criteria_results: { description: string; passed: boolean; detail: string }[];
    issues: string[];
    reviewer: string;
    created_at: string;
  }[];
}

export interface ProjectPlan {
  id: string;
  version: number;
  status: "proposed" | "approved" | "rejected" | "superseded";
  goal: string;
  scope: string;
  assumptions: string[];
  deliverables: string[];
  risks: string[];
  tasks_spec: {
    ref: string;
    title: string;
    agent: string;
    depends_on: string[];
    description?: string;
  }[];
  notes: string;
  created_by_pm: boolean;
  decided_at: string | null;
  created_at: string;
}

export interface ProjectDetail extends ProjectSummary {
  description: string;
  settings: Record<string, unknown>;
  tasks: PMTask[];
  plans: ProjectPlan[];
  task_counts: Partial<Record<PMTaskStatus, number>>;
}

export interface PMMessage {
  id: string;
  role: "owner" | "pm";
  content: string;
  meta: { intent?: { kind: string }; actions?: Record<string, unknown>[] };
  created_at: string;
}

export interface ProjectGraph {
  nodes: {
    id: string;
    ordinal: number;
    title: string;
    agent_key: string | null;
    status: PMTaskStatus;
    blocked_reason: string | null;
  }[];
  edges: { source: string; target: string }[];
}

export interface Overview {
  runs_by_status: Partial<Record<RunStatus, number>>;
  runs_total: number;
  avg_completed_duration_s: number | null;
  tool_calls_total: number;
  documents_total: number;
  tokens_input_total: number | null;
  tokens_output_total: number | null;
  recent_runs: {
    id: string;
    title: string;
    status: RunStatus;
    mode: ExecutionMode;
    created_at: string;
  }[];
}
