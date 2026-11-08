"use client";

import Link from "next/link";
import { useState } from "react";

import { can, Shell, useUser } from "@/components/Shell";
import { Button, Card, ErrorNote, Loading } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { count, label, when } from "@/lib/format";
import type { Summary } from "@/lib/types";

function Stat({ name, value, href }: { name: string; value: string; href?: string }) {
  const body = (
    <div className="rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
      <div className="text-xs uppercase text-slate-500">{name}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
    </div>
  );
  return href ? <Link href={href}>{body}</Link> : body;
}

function Bars({ data, hrefFor }: { data: Record<string, number>; hrefFor?: (key: string) => string }) {
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map(([, n]) => n));
  if (!entries.length) return <p className="text-sm text-slate-500">None.</p>;
  return (
    <ul className="space-y-1.5 text-sm">
      {entries.map(([key, n]) => {
        const name = hrefFor ? <Link className="hover:underline" href={hrefFor(key)}>{label(key)}</Link> : label(key);
        return (
          <li key={key} className="grid grid-cols-[10rem_1fr_3.5rem] items-center gap-2">
            <span className="truncate">{name}</span>
            <span className="h-2 rounded bg-slate-200 dark:bg-slate-800">
              <span className="block h-2 rounded bg-slate-600 dark:bg-slate-400" style={{ width: `${(100 * n) / max}%` }} />
            </span>
            <span className="text-right tabular-nums">{count(n)}</span>
          </li>
        );
      })}
    </ul>
  );
}

function Overview() {
  const user = useUser();
  const { data, error, loading, reload } = useApi<Summary>("/metrics/summary");
  const [detectError, setDetectError] = useState<string | null>(null);
  const [detecting, setDetecting] = useState(false);

  async function detect() {
    setDetecting(true);
    try {
      await api("/sim/detect", { method: "POST" });
      setDetectError(null);
      await reload();
    } catch (e) {
      setDetectError(e instanceof Error ? e.message : String(e));
    } finally {
      setDetecting(false);
    }
  }

  if (loading && !data) return <Loading />;
  if (error || !data) return <ErrorNote message={error} />;

  return (
    <>
      <p className="text-sm text-slate-500">
        Simulated time: <span className="font-medium text-slate-700 dark:text-slate-300">{when(data.as_of)} IST</span>
        {can(user, "admin") && (
          <Button variant="secondary" className="ml-3" onClick={detect} disabled={detecting}>
            {detecting ? "Running…" : "Run detector now"}
          </Button>
        )}
      </p>
      <ErrorNote message={detectError} />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat name="Shipments" value={count(data.shipments)} />
        <Stat name="Delivered on time" value={data.delivered_on_time_pct === null ? "–" : `${data.delivered_on_time_pct}%`} />
        <Stat name="Open exceptions" value={count(data.open_exceptions)} href="/exceptions" />
        <Stat name="Awaiting approval" value={count(data.actions_by_status.proposed ?? 0)} href="/actions" />
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <Card title="Open exceptions by rule">
          <Bars data={data.open_by_rule} hrefFor={(rule) => `/exceptions?rule=${rule}`} />
        </Card>
        <Card title="Open exceptions by severity">
          <Bars data={data.open_by_severity} hrefFor={(severity) => `/exceptions?severity=${severity}`} />
        </Card>
        <Card title="Shipments by status">
          <Bars data={data.by_status} />
        </Card>
        <Card title="Actions by status">
          <Bars data={data.actions_by_status} />
        </Card>
      </div>
    </>
  );
}

export default function Page() {
  return (
    <Shell title="Overview">
      <Overview />
    </Shell>
  );
}
