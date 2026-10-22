"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, CornerDownLeft, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { PMMessage } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

const SUGGESTIONS = [
  "Create a plan for this project",
  "Show me the status",
  "Run all ready tasks",
  "Why is #1 blocked?",
];

export function PMChat({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  const messages = useQuery({
    queryKey: ["pm-messages", projectId],
    queryFn: () => api<PMMessage[]>(`/projects/${projectId}/pm/messages`),
  });

  const send = useMutation({
    mutationFn: (content: string) =>
      api<PMMessage>(`/projects/${projectId}/pm/messages`, {
        method: "POST",
        body: JSON.stringify({ content }),
      }),
    onSuccess: () => {
      setInput("");
      void queryClient.invalidateQueries({ queryKey: ["pm-messages", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["project-graph", projectId] });
    },
    onError: (e) => toast.error(e.message),
  });

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages.data?.length, send.isPending]);

  const items = messages.data ?? [];

  return (
    <section className="flex h-[30rem] flex-col rounded-lg border border-border bg-card">
      <h3 className="flex items-center gap-2 border-b border-border px-4 py-2.5 text-sm font-semibold">
        <Bot className="size-4 text-[#635BFF]" aria-hidden /> Project Manager
      </h3>
      <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto p-4">
        {items.length === 0 && !messages.isLoading && (
          <div className="space-y-2 text-sm text-muted-foreground">
            <p>
              Tell me what you need — I plan, assign agents, enforce dependencies, and
              report only verified results. Nothing executes without your plan approval,
              and high-impact operations pause for your decision.
            </p>
          </div>
        )}
        {items.map((message) => (
          <div
            key={message.id}
            className={cn("flex", message.role === "owner" ? "justify-end" : "justify-start")}
          >
            <div
              className={cn(
                "max-w-[85%] whitespace-pre-wrap rounded-lg px-3 py-2 text-[13px] leading-relaxed",
                message.role === "owner"
                  ? "bg-[#635BFF] text-white"
                  : "border border-border bg-secondary/60 text-foreground",
              )}
            >
              {message.content}
              {message.role === "pm" && message.meta.intent?.kind && (
                <p className="mt-1 text-[10px] uppercase tracking-wide opacity-60">
                  intent: {message.meta.intent.kind.replace(/_/g, " ")}
                </p>
              )}
            </div>
          </div>
        ))}
        {send.isPending && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" aria-hidden /> PM is working…
          </div>
        )}
      </div>
      <div className="border-t border-border p-3">
        <div className="mb-2 flex flex-wrap gap-1.5">
          {SUGGESTIONS.map((suggestion) => (
            <button
              key={suggestion}
              onClick={() => setInput(suggestion)}
              className="rounded-full border border-border px-2.5 py-0.5 text-[11px] text-muted-foreground transition-colors duration-150 hover:border-[#C8CDFB] hover:text-foreground"
            >
              {suggestion}
            </button>
          ))}
        </div>
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            const text = input.trim();
            if (text && !send.isPending) send.mutate(text);
          }}
        >
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder='e.g. "assign #2 to reviewer" or "why is #3 blocked?"'
            aria-label="Message the project manager"
            className="h-9 flex-1 rounded-md border border-input bg-card px-3 text-sm outline-none transition-colors duration-150 focus-visible:border-[#635BFF] focus-visible:ring-2 focus-visible:ring-[#635BFF]/20"
          />
          <Button type="submit" size="sm" disabled={send.isPending || !input.trim()}>
            <CornerDownLeft className="size-4" aria-hidden />
          </Button>
        </form>
      </div>
    </section>
  );
}
