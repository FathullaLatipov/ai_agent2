"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, useAuth } from "@/lib/api";
import type { Me, TokenResponse } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

export default function LoginPage() {
  const router = useRouter();
  const { setToken, setMe } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function finishAuth(token: string) {
    setToken(token);
    const me = await api<Me>("/auth/me");
    setMe(me);
    router.replace("/overview");
  }

  async function submit(path: string, body: Record<string, string>) {
    setBusy(true);
    setError(null);
    try {
      const resp = await api<TokenResponse>(path, {
        method: "POST",
        body: JSON.stringify(body),
      });
      await finishAuth(resp.access_token);
    } catch (e) {
      setError(e instanceof Error ? e.message : "request failed");
    } finally {
      setBusy(false);
    }
  }

  function onLogin(form: FormData) {
    void submit("/auth/login", {
      email: String(form.get("email")),
      password: String(form.get("password")),
    });
  }

  function onRegister(form: FormData) {
    void submit("/auth/register", {
      email: String(form.get("email")),
      password: String(form.get("password")),
      full_name: String(form.get("full_name")),
      organization_name: String(form.get("organization_name")),
    });
  }

  return (
    <main className="flex flex-1 items-center justify-center bg-background p-6">
      <div className="w-full max-w-sm">
        {/* Brand */}
        <div className="mb-7 flex flex-col items-center gap-3 text-center">
          <div className="flex size-11 items-center justify-center rounded-xl bg-[#635BFF] text-lg font-bold text-white shadow-sm">
            A
          </div>
          <div>
            <h1 className="text-xl font-semibold tracking-tight">AgentOS</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Plan · execute · review · report — with a team of AI agents.
            </p>
          </div>
        </div>

        <div className="rounded-xl border border-border bg-card p-5 shadow-[0_1px_3px_rgba(16,24,40,0.06)]">
          <Tabs defaultValue="login">
            <TabsList className="mb-4 grid w-full grid-cols-2">
              <TabsTrigger value="login">Sign in</TabsTrigger>
              <TabsTrigger value="register">Create workspace</TabsTrigger>
            </TabsList>
            <TabsContent value="login">
              <form action={onLogin} className="space-y-4">
                <div className="space-y-1.5">
                  <Label htmlFor="email">Email</Label>
                  <Input id="email" name="email" type="email" required autoComplete="email" />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="password">Password</Label>
                  <Input
                    id="password"
                    name="password"
                    type="password"
                    required
                    autoComplete="current-password"
                  />
                </div>
                <Button type="submit" className="w-full" disabled={busy}>
                  {busy ? "Signing in…" : "Sign in"}
                </Button>
              </form>
            </TabsContent>
            <TabsContent value="register">
              <form action={onRegister} className="space-y-4">
                <div className="space-y-1.5">
                  <Label htmlFor="r-name">Full name</Label>
                  <Input id="r-name" name="full_name" required />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="r-org">Organization name</Label>
                  <Input id="r-org" name="organization_name" required minLength={2} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="r-email">Email</Label>
                  <Input id="r-email" name="email" type="email" required />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="r-password">Password (min 10 characters)</Label>
                  <Input
                    id="r-password"
                    name="password"
                    type="password"
                    required
                    minLength={10}
                    autoComplete="new-password"
                  />
                </div>
                <Button type="submit" className="w-full" disabled={busy}>
                  {busy ? "Creating…" : "Create workspace"}
                </Button>
              </form>
            </TabsContent>
          </Tabs>
          {error && (
            <p className="mt-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-center text-sm text-red-700" role="alert">
              {error}
            </p>
          )}
        </div>
        <p className="mt-4 text-center text-xs text-muted-foreground">
          Demo workspace: demo@example.com · agentos-demo-123
        </p>
      </div>
    </main>
  );
}
