"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { Me } from "./types";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

interface AuthState {
  token: string | null;
  me: Me | null;
  orgId: string | null;
  setToken: (token: string | null) => void;
  setMe: (me: Me | null) => void;
  logout: () => void;
}

export const useAuth = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      me: null,
      orgId: null,
      setToken: (token) => set({ token }),
      setMe: (me) =>
        set({ me, orgId: me?.memberships[0]?.organization.id ?? null }),
      logout: () => set({ token: null, me: null, orgId: null }),
    }),
    { name: "agentos-auth" },
  ),
);

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function api<T>(
  path: string,
  options: RequestInit & { raw?: boolean } = {},
): Promise<T> {
  const { token, orgId } = useAuth.getState();
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string>),
  };
  if (!(options.body instanceof FormData) && options.body) {
    headers["Content-Type"] = "application/json";
  }
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (orgId) headers["X-Organization-Id"] = orgId;

  const resp = await fetch(`${API_URL}/api/v1${path}`, { ...options, headers });
  if (resp.status === 401) {
    useAuth.getState().logout();
    throw new ApiError(401, "session expired — please sign in again");
  }
  if (!resp.ok) {
    let detail = `request failed (${resp.status})`;
    try {
      const body = await resp.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(resp.status, detail);
  }
  if (options.raw) return (await resp.text()) as unknown as T;
  if (resp.status === 204) return undefined as unknown as T;
  return (await resp.json()) as T;
}
