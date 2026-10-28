"use client";

import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CornerDownLeft, Database, FileUp, Loader2, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { DocumentInfo, RunSummary } from "@/lib/types";
import { PageHeader } from "@/components/shared";
import { RunView } from "@/components/run-view";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/ui/select";

const EXAMPLES = [
  "Analyze the last 30 days of sales, identify underperforming products, and prepare recommendations",
  "What does our travel policy say about business class flights?",
  "Prepare a customer follow-up for CUST-1001 about the open renewal and send it",
];

export default function WorkspacePage() {
  const queryClient = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [datasetId, setDatasetId] = useState<string>("");
  const [pendingDatasetId, setPendingDatasetId] = useState<string>("");
  const [dragging, setDragging] = useState(false);

  const documents = useQuery({
    queryKey: ["documents"],
    queryFn: () => api<DocumentInfo[]>("/documents"),
    refetchInterval: (q) =>
      q.state.data?.some((d) => d.status === "pending" || d.status === "processing")
        ? 2000
        : false,
  });
  const readyCsvs = (documents.data ?? []).filter(
    (d) => d.status === "ready" && d.filename.toLowerCase().endsWith(".csv"),
  );

  // Poll a just-uploaded CSV until ingestion finishes, then select it as the dataset.
  const watchDataset = async (id: string) => {
    setPendingDatasetId(id);
    try {
      for (let i = 0; i < 60; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        const doc = await api<DocumentInfo>(`/documents/${id}`);
        if (doc.status === "ready") {
          setDatasetId(doc.id);
          toast.success(`Dataset ready: ${doc.filename}`);
          void queryClient.invalidateQueries({ queryKey: ["documents"] });
          return;
        }
        if (doc.status === "failed") {
          toast.error(`Ingestion failed: ${doc.error ?? doc.filename}`);
          return;
        }
      }
      toast.error("Dataset is still processing — check the Knowledge Base page.");
    } finally {
      setPendingDatasetId("");
    }
  };

  const upload = useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return api<DocumentInfo>("/documents", { method: "POST", body: form });
    },
    onSuccess: (doc) => {
      void queryClient.invalidateQueries({ queryKey: ["documents"] });
      if (doc.status === "failed") {
        toast.error(`Upload stored but ingestion failed: ${doc.error}`);
      } else if (doc.filename.toLowerCase().endsWith(".csv")) {
        toast.success(`Uploaded ${doc.filename} — processing dataset…`);
        if (doc.status === "ready") {
          setDatasetId(doc.id);
        } else {
          void watchDataset(doc.id);
        }
      } else {
        toast.success(`Uploaded ${doc.filename} — added to the knowledge base`);
      }
    },
    onError: (e) => toast.error(e.message),
  });

  const submit = useMutation({
    mutationFn: () =>
      api<RunSummary>("/runs", {
        method: "POST",
        body: JSON.stringify({
          input_text: input.trim(),
          params: datasetId ? { dataset_document_id: datasetId } : {},
        }),
      }),
    onSuccess: (run) => {
      setActiveRunId(run.id);
      toast.success("Run submitted");
    },
    onError: (e) => toast.error(e.message),
  });

  const canSubmit = input.trim().length >= 3 && !submit.isPending;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Agent Workspace"
        description="Describe the outcome you need. The planner decomposes it into agent steps, tools execute deterministically, and the reviewer validates the result."
      />

      {/* Task composer */}
      <section
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          const file = e.dataTransfer.files?.[0];
          if (file) upload.mutate(file);
        }}
        className={
          dragging
            ? "rounded-xl border border-[#635BFF] bg-[#FAFAFF] shadow-[0_1px_2px_rgba(16,24,40,0.04)]"
            : "rounded-xl border border-border bg-card shadow-[0_1px_2px_rgba(16,24,40,0.04)]"
        }
      >
        <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-2.5">
          <div className="flex items-center gap-2">
            <Sparkles className="size-4 text-[#635BFF]" aria-hidden />
            <p className="text-sm font-semibold">New task</p>
          </div>
          <p className="text-xs text-muted-foreground">
            {dragging
              ? "Drop to upload"
              : "Drop a file anywhere on this card to add your data"}
          </p>
        </div>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && canSubmit) submit.mutate();
          }}
          rows={3}
          placeholder="Describe what you need… e.g. analyze a dataset, answer from your documents, or run a CRM workflow"
          aria-label="Task description"
          className="block w-full resize-y border-0 bg-transparent px-4 py-3 text-[14.5px] leading-relaxed outline-none placeholder:text-muted-foreground/70"
        />
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border px-3 py-2.5">
          <div className="flex min-w-0 items-center gap-2">
            <Database className="size-4 shrink-0 text-muted-foreground" aria-hidden />
            <Select
              value={datasetId || "auto"}
              onValueChange={(v) => setDatasetId(!v || v === "auto" ? "" : v)}
            >
              <SelectTrigger
                className="h-8 w-56 border-border bg-card text-[13px]"
                aria-label="Dataset"
              >
                <span className="truncate">
                  {datasetId
                    ? (readyCsvs.find((d) => d.id === datasetId)?.filename ?? "Dataset")
                    : "Dataset: auto-detect"}
                </span>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="auto">Dataset: auto-detect</SelectItem>
                {readyCsvs.map((d) => (
                  <SelectItem key={d.id} value={d.id}>
                    {d.filename}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <input
              ref={fileRef}
              type="file"
              accept=".csv,.txt,.md,.pdf"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) upload.mutate(file);
                e.target.value = "";
              }}
            />
            <Button
              variant="secondary"
              size="sm"
              onClick={() => fileRef.current?.click()}
              disabled={upload.isPending || Boolean(pendingDatasetId)}
            >
              {upload.isPending || pendingDatasetId ? (
                <Loader2 className="size-4 animate-spin" aria-hidden />
              ) : (
                <FileUp className="size-4" aria-hidden />
              )}
              {upload.isPending
                ? "Uploading…"
                : pendingDatasetId
                  ? "Processing…"
                  : "Upload data"}
            </Button>
            <p className="hidden text-xs text-muted-foreground lg:block">
              .csv → dataset · .txt .md .pdf → knowledge base
            </p>
          </div>
          <Button onClick={() => submit.mutate()} disabled={!canSubmit}>
            {submit.isPending ? (
              <Loader2 className="size-4 animate-spin" aria-hidden />
            ) : (
              <CornerDownLeft className="size-4" aria-hidden />
            )}
            {submit.isPending ? "Submitting…" : "Execute"}
          </Button>
        </div>
      </section>

      {/* Example chips */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted-foreground">Try:</span>
        {EXAMPLES.map((example) => (
          <button
            key={example}
            type="button"
            onClick={() => setInput(example)}
            className="rounded-full border border-border bg-card px-3 py-1 text-xs text-muted-foreground transition-colors duration-150 hover:border-[#C8CDFB] hover:bg-[#FAFAFF] hover:text-foreground"
          >
            {example.length > 60 ? example.slice(0, 60) + "…" : example}
          </button>
        ))}
      </div>

      {activeRunId ? (
        <RunView runId={activeRunId} />
      ) : (
        <div className="rounded-lg border border-dashed border-border bg-card/60 px-5 py-8 text-center text-sm text-muted-foreground">
          Submit a task to watch the execution plan, workflow graph, live timeline, tool
          calls, and the final report here in real time.
        </div>
      )}
    </div>
  );
}
