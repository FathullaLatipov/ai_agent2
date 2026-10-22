"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Plug, ShieldAlert, Wrench } from "lucide-react";
import { api } from "@/lib/api";
import type { McpServer, ToolInfo } from "@/lib/types";
import { ErrorState, LoadingRows, PageHeader } from "@/components/shared";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const CATEGORY_LABELS: Record<string, string> = {
  dataset: "Data analysis",
  calculator: "Math",
  sql: "Database",
  kb: "Knowledge base",
  crm: "Business (demo CRM)",
  report: "Reporting",
  mcp: "MCP (external)",
};

function categoryOf(name: string): string {
  const prefix = name.split(/[.:]/)[0];
  return CATEGORY_LABELS[prefix] ?? "Other";
}

export default function ToolsPage() {
  const tools = useQuery({ queryKey: ["tools"], queryFn: () => api<ToolInfo[]>("/tools") });
  const mcp = useQuery({
    queryKey: ["mcp"],
    queryFn: () => api<{ servers: McpServer[]; note: string }>("/tools/mcp"),
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Tools & MCP"
        description="Every tool an agent can call: typed schemas, timeouts, and approval requirements — enforced by the registry, not the UI."
      />

      {tools.isLoading ? (
        <LoadingRows />
      ) : tools.isError ? (
        <ErrorState message={tools.error.message} retry={() => tools.refetch()} />
      ) : (
        <div className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-card">
          {tools.data!.map((tool) => (
            <ToolRow key={tool.name} tool={tool} />
          ))}
        </div>
      )}

      <section className="rounded-lg border border-border bg-card">
        <h2 className="flex items-center gap-2 border-b border-border px-4 py-2.5 text-sm font-semibold">
          <Plug className="size-4 text-muted-foreground" aria-hidden /> MCP servers
        </h2>
        <div className="p-4">
          {mcp.isLoading ? (
            <p className="text-sm text-muted-foreground">Querying configured MCP servers…</p>
          ) : mcp.isError ? (
            <ErrorState message={mcp.error.message} retry={() => mcp.refetch()} />
          ) : mcp.data!.servers.length === 0 ? (
            <p className="text-sm text-muted-foreground">{mcp.data!.note}</p>
          ) : (
            <div className="space-y-3">
              <p className="text-xs text-muted-foreground">{mcp.data!.note}</p>
              {mcp.data!.servers.map((server) => (
                <div key={server.name} className="rounded-md border border-border px-3 py-2.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-sm font-medium">{server.name}</span>
                    <Badge
                      variant="outline"
                      className={cn(
                        "h-5 text-[10px]",
                        server.status === "connected"
                          ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                          : server.status === "disabled"
                            ? "text-muted-foreground"
                            : "border-red-200 bg-red-50 text-red-700",
                      )}
                    >
                      {server.status}
                    </Badge>
                    <span className="font-mono text-xs text-muted-foreground">
                      {server.command}
                    </span>
                  </div>
                  {server.tools.length > 0 && (
                    <ul className="mt-2 space-y-1">
                      {server.tools.map((tool) => (
                        <li key={tool.name} className="text-xs">
                          <span className="font-mono">{tool.name}</span>
                          <span className="text-muted-foreground"> — {tool.description}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

function ToolRow({ tool }: { tool: ToolInfo }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button
        className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors duration-150 hover:bg-secondary/50"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-secondary">
          <Wrench className="size-4 text-muted-foreground" aria-hidden />
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-[13px] font-medium">{tool.name}</span>
            {tool.requires_approval && (
              <Badge
                variant="outline"
                className="h-5 gap-1 border-amber-200 bg-amber-50 text-[10px] text-amber-800"
              >
                <ShieldAlert className="size-3" aria-hidden /> approval required
              </Badge>
            )}
            {!tool.idempotent && (
              <Badge variant="outline" className="h-5 text-[10px] text-muted-foreground">
                non-idempotent
              </Badge>
            )}
          </span>
          <span className="mt-0.5 line-clamp-1 block text-xs text-muted-foreground">
            {tool.description}
          </span>
        </span>
        <span className="hidden shrink-0 text-xs text-muted-foreground md:block">
          {categoryOf(tool.name)} · {tool.timeout_s}s
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
        <div className="space-y-3 border-t border-border bg-[#FBFBFD] px-4 py-3.5">
          <p className="text-sm text-muted-foreground">{tool.description}</p>
          <p className="text-xs text-muted-foreground">
            source: <span className="font-mono">{tool.source}</span> · permission:{" "}
            <span className="font-mono">{tool.required_permission}</span> · timeout{" "}
            {tool.timeout_s}s
          </p>
          <div className="grid gap-3 lg:grid-cols-2">
            <div>
              <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                Input schema
              </p>
              <pre className="max-h-64 overflow-auto rounded-md border border-border bg-card p-2.5 font-mono text-[11px] text-foreground/80">
                {JSON.stringify(tool.input_schema, null, 2)}
              </pre>
            </div>
            <div>
              <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                Output schema
              </p>
              <pre className="max-h-64 overflow-auto rounded-md border border-border bg-card p-2.5 font-mono text-[11px] text-foreground/80">
                {JSON.stringify(tool.output_schema, null, 2)}
              </pre>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
