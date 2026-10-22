"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FolderKanban, Plus } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { ProjectSummary } from "@/lib/types";
import {
  EmptyState,
  ErrorState,
  formatTime,
  LoadingRows,
  PageHeader,
  StatusBadge,
} from "@/components/shared";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

export default function ProjectsPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);

  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: () => api<ProjectSummary[]>("/projects"),
    refetchInterval: 15_000,
  });

  const create = useMutation({
    mutationFn: (body: { name: string; goal: string }) =>
      api<ProjectSummary>("/projects", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: (project) => {
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
      setOpen(false);
      router.push(`/projects/${project.id}`);
    },
    onError: (e) => toast.error(e.message),
  });

  return (
    <div>
      <PageHeader
        title="Projects"
        description="Delegate a goal to the AI Project Manager: it plans, assigns agents, enforces dependencies, and reports verified progress."
        actions={
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger
              className="inline-flex h-9 items-center gap-1.5 rounded-md bg-primary px-3.5 text-sm font-medium text-primary-foreground transition-colors duration-150 hover:bg-[#574FE8]"
            >
              <Plus className="size-4" aria-hidden /> New project
            </DialogTrigger>
            <DialogContent className="sm:max-w-lg">
              <DialogHeader>
                <DialogTitle>New project</DialogTitle>
                <DialogDescription>
                  Describe the goal — the PM will propose a plan for your approval.
                </DialogDescription>
              </DialogHeader>
              <form
                className="space-y-4"
                action={(form: FormData) => {
                  const name = String(form.get("name") ?? "").trim();
                  const goal = String(form.get("goal") ?? "").trim();
                  if (name.length >= 2 && goal.length >= 3) create.mutate({ name, goal });
                }}
              >
                <div className="space-y-1.5">
                  <Label htmlFor="p-name">Name</Label>
                  <Input id="p-name" name="name" required minLength={2} placeholder="Q4 sales review" />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="p-goal">Goal</Label>
                  <Textarea
                    id="p-goal"
                    name="goal"
                    required
                    minLength={3}
                    rows={3}
                    placeholder="e.g. Analyze the last 30 days of sales, find underperforming products, and prepare recommendations"
                  />
                </div>
                <Button type="submit" className="w-full" disabled={create.isPending}>
                  {create.isPending ? "Creating…" : "Create project"}
                </Button>
              </form>
            </DialogContent>
          </Dialog>
        }
      />

      {projects.isLoading ? (
        <LoadingRows rows={5} />
      ) : projects.isError ? (
        <ErrorState message={projects.error.message} retry={() => projects.refetch()} />
      ) : projects.data!.length === 0 ? (
        <EmptyState
          title="No projects yet"
          icon={FolderKanban}
          hint="Create a project and let the PM plan it, or add tasks yourself."
        />
      ) : (
        <div className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-card">
          {projects.data!.map((project) => (
            <button
              key={project.id}
              onClick={() => router.push(`/projects/${project.id}`)}
              className="flex w-full items-center gap-4 px-4 py-3.5 text-left transition-colors duration-150 hover:bg-secondary/50"
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium">{project.name}</span>
                <span className="mt-0.5 block truncate text-xs text-muted-foreground">
                  {project.goal}
                </span>
              </span>
              <span className="hidden text-xs text-muted-foreground sm:block">
                {formatTime(project.updated_at)}
              </span>
              <StatusBadge status={project.status} />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
