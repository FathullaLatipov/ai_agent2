"use client";

import { Suspense, use, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { api, useAuth } from "@/lib/api";
import type { InvitePublic, Me, TokenResponse } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";

const ROLE_LABELS: Record<string, string> = {
  admin: "Admin",
  member: "Member",
  viewer: "Viewer",
};

export default function JoinPage({ params }: { params: Promise<{ token: string }> }) {
  return (
    <Suspense
      fallback={
        <main className="flex flex-1 items-center justify-center bg-background p-6">
          <Skeleton className="h-64 w-full max-w-sm rounded-xl" />
        </main>
      }
    >
      <JoinForm params={params} />
    </Suspense>
  );
}

function JoinForm({ params }: { params: Promise<{ token: string }> }) {
  const { token } = use(params);
  const router = useRouter();
  const { setToken, setMe } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const invite = useQuery({
    queryKey: ["invite", token],
    queryFn: () => api<InvitePublic>(`/invites/${token}`),
    retry: false,
  });

  async function onAccept(form: FormData) {
    setBusy(true);
    setError(null);
    try {
      const resp = await api<TokenResponse>(`/invites/${token}/accept`, {
        method: "POST",
        body: JSON.stringify({
          full_name: String(form.get("full_name")),
          password: String(form.get("password")),
        }),
      });
      setToken(resp.access_token);
      const me = await api<Me>("/auth/me");
      setMe(me);
      router.replace("/overview");
    } catch (e) {
      setError(e instanceof Error ? e.message : "request failed");
      setBusy(false);
    }
  }

  return (
    <main className="flex flex-1 items-center justify-center bg-background p-6">
      <div className="w-full max-w-sm">
        <div className="mb-7 flex flex-col items-center gap-3 text-center">
          <div className="flex size-11 items-center justify-center rounded-xl bg-[#635BFF] text-lg font-bold text-white shadow-sm">
            A
          </div>
          <div>
            <h1 className="text-xl font-semibold tracking-tight">Join a workspace</h1>
            {invite.data && (
              <p className="mt-1 text-sm text-muted-foreground">
                You were invited to <strong>{invite.data.organization_name}</strong> as{" "}
                {ROLE_LABELS[invite.data.role] ?? invite.data.role}.
              </p>
            )}
          </div>
        </div>

        <div className="rounded-xl border border-border bg-card p-5 shadow-[0_1px_3px_rgba(16,24,40,0.06)]">
          {invite.isLoading ? (
            <div className="space-y-3">
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
            </div>
          ) : invite.isError ? (
            <p className="text-sm text-red-600">
              This invite link is not valid — it may have been mistyped. Ask your admin
              for a new one.
            </p>
          ) : !invite.data!.valid ? (
            <p className="text-sm text-amber-700">
              {invite.data!.reason ?? "This invite is no longer valid."} Ask your admin
              for a new link.
            </p>
          ) : (
            <form className="space-y-4" action={onAccept}>
              <div className="space-y-1.5">
                <Label htmlFor="email">Email</Label>
                <Input id="email" value={invite.data!.email} disabled />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="full_name">Full name</Label>
                <Input id="full_name" name="full_name" required autoComplete="name" />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="password">Password</Label>
                <Input
                  id="password"
                  name="password"
                  type="password"
                  required
                  minLength={10}
                  autoComplete="new-password"
                />
                <p className="text-xs text-muted-foreground">At least 10 characters.</p>
              </div>
              {error && <p className="text-sm text-red-600">{error}</p>}
              <Button type="submit" className="w-full" disabled={busy}>
                {busy ? "Joining…" : "Join workspace"}
              </Button>
            </form>
          )}
        </div>
      </div>
    </main>
  );
}
