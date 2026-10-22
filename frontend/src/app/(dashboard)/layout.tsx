import { Suspense } from "react";
import { AppShell } from "@/components/app-shell";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center text-sm text-muted-foreground">
          Loading…
        </div>
      }
    >
      <AppShell>{children}</AppShell>
    </Suspense>
  );
}
