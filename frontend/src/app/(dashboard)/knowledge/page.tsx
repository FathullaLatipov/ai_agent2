"use client";

import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, FileUp, Search, Trash2, UploadCloud } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { DocumentInfo, SearchHit } from "@/lib/types";
import {
  EmptyState,
  ErrorState,
  formatTime,
  LoadingRows,
  PageHeader,
  StatusBadge,
} from "@/components/shared";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

export default function KnowledgePage() {
  const queryClient = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [dragging, setDragging] = useState(false);
  const [results, setResults] = useState<{
    hits: SearchHit[];
    method: string;
    note: string;
  } | null>(null);

  const documents = useQuery({
    queryKey: ["documents"],
    queryFn: () => api<DocumentInfo[]>("/documents"),
    refetchInterval: (q) =>
      q.state.data?.some((d) => d.status === "pending" || d.status === "processing")
        ? 3000
        : false,
  });

  const upload = useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return api<DocumentInfo>("/documents", { method: "POST", body: form });
    },
    onSuccess: (doc) => {
      toast.success(
        doc.status === "failed"
          ? `Upload stored but ingestion failed: ${doc.error}`
          : `Uploaded ${doc.filename}`,
      );
      void queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
    onError: (e) => toast.error(e.message),
  });

  const remove = useMutation({
    mutationFn: (id: string) => api(`/documents/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("Document deleted");
      void queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
    onError: (e) => toast.error(e.message),
  });

  const search = useMutation({
    mutationFn: () =>
      api<{ hits: SearchHit[]; method: string; note: string }>("/documents/search", {
        method: "POST",
        body: JSON.stringify({ query }),
      }),
    onSuccess: setResults,
    onError: (e) => toast.error(e.message),
  });

  return (
    <div className="space-y-5">
      <PageHeader
        title="Knowledge Base"
        description="Documents are extracted, chunked, and embedded; research agents ground answers in these sources. CSV files become datasets for the analyst."
        actions={
          <>
            <input
              ref={fileRef}
              type="file"
              accept=".txt,.md,.csv,.pdf"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) upload.mutate(file);
                e.target.value = "";
              }}
            />
            <Button onClick={() => fileRef.current?.click()} disabled={upload.isPending}>
              <FileUp className="size-4" aria-hidden />
              {upload.isPending ? "Uploading…" : "Upload document"}
            </Button>
          </>
        }
      />

      {/* Drag & drop zone */}
      <button
        type="button"
        onClick={() => fileRef.current?.click()}
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
        className={cn(
          "flex w-full flex-col items-center justify-center gap-1.5 rounded-lg border border-dashed px-4 py-6 transition-colors duration-150",
          dragging
            ? "border-[#635BFF] bg-[#FAFAFF]"
            : "border-border bg-card hover:border-[#C8CDFB] hover:bg-[#FCFCFE]",
        )}
        aria-label="Upload a document by clicking or dropping a file"
      >
        <UploadCloud
          className={cn("size-5", dragging ? "text-[#635BFF]" : "text-muted-foreground")}
          aria-hidden
        />
        <p className="text-sm font-medium">
          {dragging ? "Drop to upload" : "Drop a file here or click to browse"}
        </p>
        <p className="text-xs text-muted-foreground">
          .txt, .md, .pdf (knowledge) · .csv (datasets) · max 25 MB · duplicates rejected
        </p>
      </button>

      {/* Search */}
      <section className="rounded-lg border border-border bg-card">
        <h2 className="border-b border-border px-4 py-2.5 text-sm font-semibold">
          Search the knowledge base
        </h2>
        <div className="p-4">
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (query.trim().length >= 2) search.mutate();
            }}
          >
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="e.g. business class flights"
              aria-label="Search query"
            />
            <Button type="submit" variant="secondary" disabled={search.isPending}>
              <Search className="size-4" aria-hidden /> Search
            </Button>
          </form>
          {results && (
            <div className="mt-4 space-y-2">
              <p className="text-xs text-muted-foreground">
                method: <span className="font-mono">{results.method}</span>
                {results.note ? ` — ${results.note}` : ""}
              </p>
              {results.hits.length === 0 ? (
                <p className="text-sm text-muted-foreground">No matches.</p>
              ) : (
                results.hits.map((hit) => (
                  <div key={hit.chunk_id} className="rounded-md border border-border px-3 py-2.5">
                    <p className="text-xs text-muted-foreground">
                      {hit.filename} · chunk {hit.ordinal} · score{" "}
                      <span className="font-mono">{hit.score.toFixed(4)}</span>
                    </p>
                    <p className="mt-1 text-sm leading-relaxed">{hit.excerpt}</p>
                  </div>
                ))
              )}
            </div>
          )}
        </div>
      </section>

      {/* Documents */}
      {documents.isLoading ? (
        <LoadingRows />
      ) : documents.isError ? (
        <ErrorState message={documents.error.message} retry={() => documents.refetch()} />
      ) : documents.data!.length === 0 ? (
        <EmptyState
          title="No documents yet"
          icon={BookOpen}
          hint="Upload .txt, .md, .pdf for knowledge or .csv as datasets. Identical files are deduplicated per workspace."
        />
      ) : (
        <section className="overflow-x-auto rounded-lg border border-border bg-card">
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>File</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="hidden text-right sm:table-cell">Chunks</TableHead>
                <TableHead className="hidden md:table-cell">Embeddings</TableHead>
                <TableHead className="hidden sm:table-cell">Uploaded</TableHead>
                <TableHead className="w-10" aria-label="Actions" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {documents.data!.map((doc) => (
                <TableRow key={doc.id}>
                  <TableCell className="max-w-0 w-full">
                    <p className="truncate text-sm font-medium">{doc.filename}</p>
                    <p className="text-xs text-muted-foreground">
                      {(doc.size_bytes / 1024).toFixed(1)} KB · {doc.content_type}
                    </p>
                    {doc.error && <p className="mt-0.5 text-xs text-red-600">{doc.error}</p>}
                  </TableCell>
                  <TableCell>
                    <StatusBadge status={doc.status} />
                  </TableCell>
                  <TableCell className="hidden text-right font-mono text-xs tabular-nums sm:table-cell">
                    {doc.chunk_count}
                  </TableCell>
                  <TableCell className="hidden text-xs text-muted-foreground md:table-cell">
                    {doc.embeddings_provider ?? "—"}
                  </TableCell>
                  <TableCell className="hidden whitespace-nowrap text-xs text-muted-foreground sm:table-cell">
                    {formatTime(doc.created_at)}
                  </TableCell>
                  <TableCell>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Delete ${doc.filename}`}
                      onClick={() => {
                        if (confirm(`Delete ${doc.filename}? This cannot be undone.`)) {
                          remove.mutate(doc.id);
                        }
                      }}
                    >
                      <Trash2 className="size-4 text-muted-foreground" aria-hidden />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </section>
      )}
    </div>
  );
}
