"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/api";

export default function Home() {
  const router = useRouter();
  const token = useAuth((s) => s.token);

  useEffect(() => {
    router.replace(token ? "/overview" : "/login");
  }, [token, router]);

  return (
    <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
      Loading AgentOS…
    </div>
  );
}
