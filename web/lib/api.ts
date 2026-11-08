"use client";

import { useCallback, useEffect, useState } from "react";

// The token lives in localStorage: fine for a local demo, but readable by any script on the page.
// For a real deployment, have the API set an httpOnly cookie instead.
const TOKEN_KEY = "courier-ops-token";

export const getToken = () => (typeof window === "undefined" ? null : window.localStorage.getItem(TOKEN_KEY));
export const setToken = (token: string) => window.localStorage.setItem(TOKEN_KEY, token);
export const clearToken = () => window.localStorage.removeItem(TOKEN_KEY);

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

function detailMessage(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => (d as { msg?: string }).msg ?? String(d)).join("; ");
  return fallback;
}

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, ...rest } = init;
  const headers = new Headers(rest.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (json !== undefined) headers.set("Content-Type", "application/json");
  const res = await fetch(`/api${path}`, { ...rest, headers, body: json !== undefined ? JSON.stringify(json) : rest.body });
  if (res.status === 401 && path !== "/auth/token") {
    clearToken();
    window.location.href = "/login";
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, detailMessage(body, res.statusText));
  return body as T;
}

export async function login(username: string, password: string): Promise<void> {
  const form = new URLSearchParams({ username, password });
  const { access_token } = await api<{ access_token: string }>("/auth/token", { method: "POST", body: form });
  setToken(access_token);
}

/** Fetch `path` on mount and whenever it changes. `path = null` skips fetching. */
export function useApi<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(path !== null);

  const reload = useCallback(async () => {
    if (path === null) return;
    setLoading(true);
    try {
      setData(await api<T>(path));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { data, error, loading, reload, setData };
}
