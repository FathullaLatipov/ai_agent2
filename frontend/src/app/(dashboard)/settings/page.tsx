"use client";

import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, Info, XCircle } from "lucide-react";
import { api, useAuth } from "@/lib/api";
import type { Capabilities } from "@/lib/types";
import { ErrorState, LoadingRows, PageHeader } from "@/components/shared";

export default function SettingsPage() {
  const me = useAuth((s) => s.me);
  const capabilities = useQuery({
    queryKey: ["capabilities"],
    queryFn: () => api<Capabilities>("/meta/capabilities"),
  });

  if (capabilities.isLoading) return <LoadingRows />;
  if (capabilities.isError)
    return (
      <ErrorState message={capabilities.error.message} retry={() => capabilities.refetch()} />
    );
  const caps = capabilities.data!;

  return (
    <div className="space-y-5">
      <PageHeader
        title="Settings"
        description="Provider status and execution limits, read from server configuration. Secrets never leave the environment."
      />

      <div className="grid items-start gap-4 md:grid-cols-2">
        <Section title="Workspace">
          <dl className="space-y-2 text-sm">
            <Row label="Organization" value={me?.memberships[0]?.organization.name ?? "—"} />
            <Row label="Your role" value={me?.memberships[0]?.role ?? "—"} />
            <Row label="Signed in as" value={me?.user.email ?? "—"} />
            <Row label="Environment" value={caps.environment} />
          </dl>
        </Section>

        <Section title="AI provider">
          <div className="space-y-2.5 text-sm">
            <StatusLine
              ok={caps.llm.configured}
              okText={`${caps.llm.provider} · ${caps.llm.model}`}
              failText="Not configured — runs execute in deterministic mode"
            />
            {caps.llm.note && <Note text={caps.llm.note} />}
            <p className="text-xs text-muted-foreground">
              Configure via <code className="font-mono">AGENTOS_LLM_PROVIDER</code> /{" "}
              <code className="font-mono">AGENTOS_ANTHROPIC_API_KEY</code> in the server
              environment. Keys are never stored in the database or shown here.
            </p>
          </div>
        </Section>

        <Section title="Embeddings & retrieval">
          <div className="space-y-2.5 text-sm">
            <p>
              <span className="text-muted-foreground">Provider:</span>{" "}
              <span className="font-mono">{caps.embeddings.provider}</span>{" "}
              <span className="text-muted-foreground">
                {caps.embeddings.semantic ? "(semantic)" : "(lexical, deterministic)"}
              </span>
            </p>
            {caps.embeddings.note && <Note text={caps.embeddings.note} />}
            <StatusLine
              ok={caps.vector_search.pgvector}
              okText="pgvector extension active — SQL cosine search"
              failText={`pgvector unavailable — using ${caps.vector_search.path}`}
              failIsInfo
            />
          </div>
        </Section>

        <Section title="Execution limits">
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm">
            {Object.entries(caps.limits).map(([key, value]) => (
              <div key={key} className="contents">
                <dt className="text-muted-foreground">{key.replace(/_/g, " ")}</dt>
                <dd className="text-right font-mono tabular-nums">{value}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-3 text-xs text-muted-foreground">
            Budgets are enforced by the engine in code — not by prompts. Change them via{" "}
            <code className="font-mono">AGENTOS_*</code> environment variables.
          </p>
        </Section>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-border bg-card">
      <h2 className="border-b border-border px-4 py-2.5 text-sm font-semibold">{title}</h2>
      <div className="p-4">{children}</div>
    </section>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="truncate text-right">{value}</dd>
    </div>
  );
}

function StatusLine({
  ok,
  okText,
  failText,
  failIsInfo = false,
}: {
  ok: boolean;
  okText: string;
  failText: string;
  failIsInfo?: boolean;
}) {
  return (
    <p className="flex items-center gap-2">
      {ok ? (
        <CheckCircle2 className="size-4 shrink-0 text-emerald-600" aria-hidden />
      ) : failIsInfo ? (
        <Info className="size-4 shrink-0 text-blue-600" aria-hidden />
      ) : (
        <XCircle className="size-4 shrink-0 text-amber-600" aria-hidden />
      )}
      <span>{ok ? okText : failText}</span>
    </p>
  );
}

function Note({ text }: { text: string }) {
  return (
    <p className="flex items-start gap-2 rounded-md bg-secondary/70 px-2.5 py-2 text-xs text-muted-foreground">
      <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden />
      {text}
    </p>
  );
}
