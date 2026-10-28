"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Activity,
  BookOpen,
  Bot,
  CheckSquare,
  ChevronDown,
  FolderKanban,
  LayoutDashboard,
  ListTodo,
  LogOut,
  Menu,
  Settings,
  Users,
  Wrench,
  X,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { api, useAuth } from "@/lib/api";
import type { Approval } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

const NAV_GROUPS = [
  {
    label: "Workspace",
    items: [
      { href: "/overview", label: "Overview", icon: LayoutDashboard },
      { href: "/projects", label: "Projects", icon: FolderKanban },
      { href: "/workspace", label: "Agent Workspace", icon: Activity },
      { href: "/runs", label: "Runs", icon: ListTodo },
      { href: "/approvals", label: "Approvals", icon: CheckSquare },
    ],
  },
  {
    label: "AI Platform",
    items: [
      { href: "/agents", label: "Agents", icon: Bot },
      { href: "/tools", label: "Tools & MCP", icon: Wrench },
      { href: "/knowledge", label: "Knowledge Base", icon: BookOpen },
    ],
  },
  {
    label: "System",
    items: [
      { href: "/members", label: "Members", icon: Users, adminOnly: true },
      { href: "/settings", label: "Settings", icon: Settings },
    ],
  },
] as const;

const PAGE_TITLES: Record<string, string> = {
  "/overview": "Overview",
  "/projects": "Projects",
  "/workspace": "Agent Workspace",
  "/runs": "Runs",
  "/approvals": "Approvals",
  "/agents": "Agents",
  "/tools": "Tools & MCP",
  "/knowledge": "Knowledge Base",
  "/members": "Members",
  "/settings": "Settings",
};

function currentTitle(pathname: string): string {
  const match = Object.keys(PAGE_TITLES).find((key) => pathname.startsWith(key));
  return match ? PAGE_TITLES[match] : "AgentOS";
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { token, me, logout } = useAuth();
  // `mounted` keeps server and first-client render identical (the auth store
  // hydrates from localStorage); the redirect fires only after mount, and the
  // page segment always renders during prerender (fixes the Next.js
  // "segment dropped from rendering" instant-navigation insight).
  const mounted = useSyncExternalStore(
    emptySubscribe,
    () => true,
    () => false,
  );
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    if (mounted && !token) router.replace("/login");
  }, [mounted, token, router]);

  const pendingApprovals = useQuery({
    queryKey: ["approvals", "pending-count"],
    queryFn: () => api<Approval[]>("/approvals?status=pending"),
    enabled: mounted && Boolean(token),
    refetchInterval: 15_000,
  });
  const pendingCount = pendingApprovals.data?.length ?? 0;

  const signedOut = mounted && !token;
  const role = me?.memberships[0]?.role;
  const isAdmin = role === "owner" || role === "admin";

  const nav = (
    <nav className="flex-1 space-y-5 overflow-y-auto px-3 py-4" aria-label="Main">
      {NAV_GROUPS.map((group) => (
        <div key={group.label}>
          <p className="mb-1 px-2.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/70">
            {group.label}
          </p>
          <div className="space-y-0.5">
            {group.items
              .filter((item) => isAdmin || !("adminOnly" in item && item.adminOnly))
              .map(({ href, label, icon: Icon }) => {
              const active = pathname.startsWith(href);
              return (
                <Link
                  key={href}
                  href={href}
                  onClick={() => setMobileOpen(false)}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "group flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13.5px] transition-colors duration-150",
                    active
                      ? "bg-[#EEF0FE] font-medium text-[#4F48D6]"
                      : "text-[#404654] hover:bg-secondary hover:text-foreground",
                  )}
                >
                  <Icon
                    className={cn(
                      "size-4",
                      active ? "text-[#635BFF]" : "text-muted-foreground group-hover:text-foreground",
                    )}
                    aria-hidden
                  />
                  <span className="flex-1">{label}</span>
                  {href === "/approvals" && pendingCount > 0 && (
                    <span className="flex h-5 min-w-5 items-center justify-center rounded-full bg-amber-100 px-1.5 text-[11px] font-semibold text-amber-800">
                      {pendingCount}
                    </span>
                  )}
                </Link>
              );
            })}
          </div>
        </div>
      ))}
    </nav>
  );

  const brand = (
    <div className="flex h-14 items-center gap-2.5 border-b border-sidebar-border px-4">
      <div className="flex size-7 items-center justify-center rounded-md bg-[#635BFF] text-[13px] font-bold text-white shadow-sm">
        A
      </div>
      <div className="leading-none">
        <p className="text-sm font-semibold tracking-tight">AgentOS</p>
        <p className="mt-0.5 text-[11px] text-muted-foreground">Agent platform</p>
      </div>
    </div>
  );

  return (
    <div className="flex min-h-screen w-full">
      {/* Desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-60 flex-col border-r border-sidebar-border bg-sidebar md:flex">
        {brand}
        {nav}
      </aside>

      {/* Mobile slide-over */}
      {mobileOpen && (
        <div className="fixed inset-0 z-40 md:hidden">
          <button
            aria-label="Close navigation"
            className="absolute inset-0 bg-foreground/20"
            onClick={() => setMobileOpen(false)}
          />
          <aside className="absolute inset-y-0 left-0 flex w-64 flex-col border-r border-sidebar-border bg-sidebar shadow-xl">
            <div className="flex items-center justify-between pr-2">
              {brand}
              <Button
                variant="ghost"
                size="icon"
                aria-label="Close menu"
                onClick={() => setMobileOpen(false)}
              >
                <X className="size-4" aria-hidden />
              </Button>
            </div>
            {nav}
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col md:pl-60">
        {/* Top bar */}
        <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-border bg-background/90 px-4 backdrop-blur md:px-8">
          <Button
            variant="ghost"
            size="icon"
            className="md:hidden"
            aria-label="Open navigation"
            onClick={() => setMobileOpen(true)}
          >
            <Menu className="size-5" aria-hidden />
          </Button>
          <p className="min-w-0 truncate text-sm text-muted-foreground">
            <span className="hidden sm:inline">AgentOS</span>
            <span className="hidden px-1.5 text-border sm:inline">/</span>
            <span className="font-medium text-foreground">{currentTitle(pathname)}</span>
          </p>
          <div className="ml-auto flex items-center gap-2">
            {pendingCount > 0 && (
              <Link
                href="/approvals"
                className="hidden items-center gap-1.5 rounded-full border border-amber-200 bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-800 transition-colors duration-150 hover:bg-amber-100 sm:inline-flex"
              >
                <CheckSquare className="size-3.5" aria-hidden />
                {pendingCount} pending approval{pendingCount > 1 ? "s" : ""}
              </Link>
            )}
            <DropdownMenu>
              <DropdownMenuTrigger
                className="flex items-center gap-2 rounded-md border border-transparent px-2 py-1.5 text-sm transition-colors duration-150 hover:bg-secondary focus-visible:outline-2 focus-visible:outline-ring"
                aria-label="User menu"
              >
                <span className="flex size-7 items-center justify-center rounded-full bg-[#EEF0FE] text-xs font-semibold text-[#4F48D6]">
                  {mounted && me ? initials(me.user.full_name) : "·"}
                </span>
                <ChevronDown className="size-3.5 text-muted-foreground" aria-hidden />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-60">
                <DropdownMenuLabel>
                  <p className="truncate text-sm font-medium">
                    {mounted && me ? me.user.full_name : "Account"}
                  </p>
                  <p className="truncate text-xs font-normal text-muted-foreground">
                    {mounted && me
                      ? `${me.memberships[0]?.organization.name} · ${me.memberships[0]?.role}`
                      : ""}
                  </p>
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={() => router.push("/settings")}>
                  <Settings className="size-4" aria-hidden /> Settings
                </DropdownMenuItem>
                <DropdownMenuItem
                  onClick={() => {
                    logout();
                    router.replace("/login");
                  }}
                >
                  <LogOut className="size-4" aria-hidden /> Sign out
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </header>

        <main className="flex-1 px-4 py-6 md:px-8 md:py-7">
          {signedOut ? (
            <div className="flex h-64 items-center justify-center text-sm text-muted-foreground">
              Redirecting to sign-in…
            </div>
          ) : (
            <div className="mx-auto w-full max-w-6xl">{children}</div>
          )}
        </main>
      </div>
    </div>
  );
}

function emptySubscribe(): () => void {
  return () => {};
}

function initials(name: string): string {
  return (
    name
      .split(/\s+/)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase() ?? "")
      .join("") || "U"
  );
}
