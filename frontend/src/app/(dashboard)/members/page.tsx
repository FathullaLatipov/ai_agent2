"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, Mail, ShieldAlert, Trash2, UserPlus, Users } from "lucide-react";
import { toast } from "sonner";
import { api, useAuth } from "@/lib/api";
import type { InviteCreated, InviteInfo, MemberInfo } from "@/lib/types";
import {
  EmptyState,
  ErrorState,
  formatTime,
  LoadingRows,
  PageHeader,
} from "@/components/shared";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const ROLE_LABELS: Record<string, string> = {
  owner: "Owner",
  admin: "Admin",
  member: "Member",
  viewer: "Viewer",
};

const GRANTABLE = ["admin", "member", "viewer"] as const;

function joinUrl(token: string): string {
  return `${window.location.origin}/join/${token}`;
}

export default function MembersPage() {
  const me = useAuth((s) => s.me);
  const queryClient = useQueryClient();
  const myRole = me?.memberships[0]?.role;
  const isAdmin = myRole === "owner" || myRole === "admin";

  const [email, setEmail] = useState("");
  const [role, setRole] = useState<string>("member");
  const [created, setCreated] = useState<InviteCreated | null>(null);
  const [copied, setCopied] = useState(false);

  const members = useQuery({
    queryKey: ["members"],
    queryFn: () => api<MemberInfo[]>("/org/members"),
    enabled: isAdmin,
  });
  const invites = useQuery({
    queryKey: ["invites"],
    queryFn: () => api<InviteInfo[]>("/org/invites"),
    enabled: isAdmin,
  });

  const invite = useMutation({
    mutationFn: () =>
      api<InviteCreated>("/org/invites", {
        method: "POST",
        body: JSON.stringify({ email: email.trim(), role }),
      }),
    onSuccess: (inv) => {
      setCreated(inv);
      setCopied(false);
      setEmail("");
      void queryClient.invalidateQueries({ queryKey: ["invites"] });
    },
    onError: (e) => toast.error(e.message),
  });

  const revoke = useMutation({
    mutationFn: (id: string) => api(`/org/invites/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("Invite revoked");
      void queryClient.invalidateQueries({ queryKey: ["invites"] });
    },
    onError: (e) => toast.error(e.message),
  });

  const changeRole = useMutation({
    mutationFn: ({ id, role: newRole }: { id: string; role: string }) =>
      api<MemberInfo>(`/org/members/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ role: newRole }),
      }),
    onSuccess: (m) => {
      toast.success(`${m.email} is now ${ROLE_LABELS[m.role]}`);
      void queryClient.invalidateQueries({ queryKey: ["members"] });
    },
    onError: (e) => toast.error(e.message),
  });

  const remove = useMutation({
    mutationFn: (id: string) => api(`/org/members/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("Member removed");
      void queryClient.invalidateQueries({ queryKey: ["members"] });
    },
    onError: (e) => toast.error(e.message),
  });

  if (!isAdmin) {
    return (
      <EmptyState
        title="Admins only"
        icon={ShieldAlert}
        hint="Team management is available to workspace owners and admins."
      />
    );
  }

  const canInvite = /\S+@\S+\.\S+/.test(email.trim()) && !invite.isPending;

  return (
    <div className="space-y-5">
      <PageHeader
        title="Members"
        description="Invite teammates with a one-time link, assign roles, and remove access. Role determines what they can see and do."
      />

      {/* Invite form */}
      <section className="rounded-lg border border-border bg-card">
        <h2 className="border-b border-border px-4 py-2.5 text-sm font-semibold">
          Invite a teammate
        </h2>
        <form
          className="flex flex-wrap items-center gap-2 p-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (canInvite) invite.mutate();
          }}
        >
          <div className="relative min-w-60 flex-1">
            <Mail
              className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground"
              aria-hidden
            />
            <Input
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              type="email"
              placeholder="teammate@company.com"
              aria-label="Email to invite"
              className="pl-8"
            />
          </div>
          <Select value={role} onValueChange={(v) => v && setRole(v)}>
            <SelectTrigger className="h-8 w-32" aria-label="Role">
              <span>{ROLE_LABELS[role]}</span>
            </SelectTrigger>
            <SelectContent>
              {GRANTABLE.map((r) => (
                <SelectItem key={r} value={r}>
                  {ROLE_LABELS[r]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button type="submit" disabled={!canInvite}>
            <UserPlus className="size-4" aria-hidden />
            {invite.isPending ? "Creating…" : "Create invite link"}
          </Button>
          <p className="w-full text-xs text-muted-foreground">
            Admin — manages the team · Member — runs projects and uploads data · Viewer —
            read-only.
          </p>
        </form>
      </section>

      {/* One-time link dialog */}
      <Dialog open={created !== null} onOpenChange={(open) => !open && setCreated(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Invite link created</DialogTitle>
            <DialogDescription>
              Send this link to {created?.email}. It can be used once and expires in 7
              days. For security it is shown only now — copy it before closing.
            </DialogDescription>
          </DialogHeader>
          {created && (
            <div className="flex items-center gap-2">
              <Input
                readOnly
                value={joinUrl(created.token)}
                onFocus={(e) => e.target.select()}
                aria-label="Invite link"
                className="font-mono text-xs"
              />
              <Button
                variant="secondary"
                onClick={() => {
                  void navigator.clipboard
                    .writeText(joinUrl(created.token))
                    .then(() => setCopied(true));
                }}
              >
                {copied ? (
                  <Check className="size-4 text-emerald-600" aria-hidden />
                ) : (
                  <Copy className="size-4" aria-hidden />
                )}
                {copied ? "Copied" : "Copy"}
              </Button>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* Pending invites */}
      {(invites.data?.length ?? 0) > 0 && (
        <section className="overflow-x-auto rounded-lg border border-border bg-card">
          <h2 className="border-b border-border px-4 py-2.5 text-sm font-semibold">
            Pending invites
          </h2>
          <Table>
            <TableBody>
              {invites.data!.map((inv) => (
                <TableRow key={inv.id}>
                  <TableCell className="text-sm font-medium">{inv.email}</TableCell>
                  <TableCell>
                    <Badge variant="secondary">{ROLE_LABELS[inv.role]}</Badge>
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                    expires {formatTime(inv.expires_at)}
                  </TableCell>
                  <TableCell className="w-10">
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Revoke invite for ${inv.email}`}
                      onClick={() => revoke.mutate(inv.id)}
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

      {/* Members table */}
      {members.isLoading ? (
        <LoadingRows />
      ) : members.isError ? (
        <ErrorState message={members.error.message} retry={() => members.refetch()} />
      ) : members.data!.length === 0 ? (
        <EmptyState title="No members yet" icon={Users} hint="Invite your first teammate." />
      ) : (
        <section className="overflow-x-auto rounded-lg border border-border bg-card">
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>Member</TableHead>
                <TableHead>Role</TableHead>
                <TableHead className="hidden sm:table-cell">Joined</TableHead>
                <TableHead className="w-10" aria-label="Actions" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {members.data!.map((m) => {
                const isSelf = m.user_id === me?.user.id;
                const locked = m.role === "owner" || isSelf;
                return (
                  <TableRow key={m.id}>
                    <TableCell className="max-w-0 w-full">
                      <p className="truncate text-sm font-medium">
                        {m.full_name || m.email}
                        {isSelf && (
                          <span className="ml-1.5 text-xs text-muted-foreground">(you)</span>
                        )}
                      </p>
                      <p className="truncate text-xs text-muted-foreground">{m.email}</p>
                    </TableCell>
                    <TableCell>
                      {locked ? (
                        <Badge variant="secondary">{ROLE_LABELS[m.role]}</Badge>
                      ) : (
                        <Select
                          value={m.role}
                          onValueChange={(v) => v && changeRole.mutate({ id: m.id, role: v })}
                        >
                          <SelectTrigger
                            className="h-7 w-28 text-xs"
                            aria-label={`Role for ${m.email}`}
                          >
                            <span>{ROLE_LABELS[m.role]}</span>
                          </SelectTrigger>
                          <SelectContent>
                            {GRANTABLE.map((r) => (
                              <SelectItem key={r} value={r}>
                                {ROLE_LABELS[r]}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      )}
                    </TableCell>
                    <TableCell className="hidden whitespace-nowrap text-xs text-muted-foreground sm:table-cell">
                      {formatTime(m.created_at)}
                    </TableCell>
                    <TableCell>
                      {!locked && (
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={`Remove ${m.email}`}
                          onClick={() => {
                            if (
                              confirm(
                                `Remove ${m.email} from the workspace? They will lose access immediately.`,
                              )
                            ) {
                              remove.mutate(m.id);
                            }
                          }}
                        >
                          <Trash2 className="size-4 text-muted-foreground" aria-hidden />
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </section>
      )}
    </div>
  );
}
