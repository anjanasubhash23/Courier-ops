"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import { api, clearToken, getToken } from "@/lib/api";
import { ROLE_RANK, type Me, type Role } from "@/lib/types";

const UserContext = createContext<Me | null>(null);

/** The signed-in user. Only usable inside <Shell>. */
export function useUser(): Me {
  const user = useContext(UserContext);
  if (!user) throw new Error("useUser outside <Shell>");
  return user;
}

export const can = (user: Me, role: Role) => ROLE_RANK[user.role] >= ROLE_RANK[role];

const NAV = [
  { href: "/", label: "Overview" },
  { href: "/exceptions", label: "Exceptions" },
  { href: "/actions", label: "Approvals" },
  { href: "/ask", label: "Ask" },
  { href: "/hubs", label: "Hubs" },
];

/** Signed-in page frame: checks the token, then shows the navigation and the page. */
export function Shell({ title, children }: { title: string; children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [user, setUser] = useState<Me | null>(null);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    api<Me>("/auth/me")
      .then(setUser)
      .catch(() => router.replace("/login"));
  }, [router]);

  if (!user) return null;

  return (
    <UserContext.Provider value={user}>
      <header className="border-b border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <span className="font-semibold">Courier Ops Copilot</span>
          <nav className="flex flex-wrap gap-4 text-sm">
            {NAV.map(({ href, label }) => {
              const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
              return (
                <Link
                  key={href}
                  href={href}
                  className={active ? "font-medium text-slate-900 dark:text-white" : "text-slate-500 hover:text-slate-900 dark:hover:text-white"}
                >
                  {label}
                </Link>
              );
            })}
          </nav>
          <span className="ml-auto flex items-center gap-3 text-sm text-slate-500">
            {user.username} · {user.role}
            <button
              className="underline hover:text-slate-900 dark:hover:text-white"
              onClick={() => {
                clearToken();
                router.replace("/login");
              }}
            >
              Sign out
            </button>
          </span>
        </div>
      </header>
      <main className="mx-auto max-w-6xl space-y-4 px-4 py-6">
        <h1 className="text-xl font-semibold">{title}</h1>
        {children}
      </main>
    </UserContext.Provider>
  );
}
