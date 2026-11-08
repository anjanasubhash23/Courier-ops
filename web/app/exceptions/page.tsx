"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { Shell } from "@/components/Shell";
import { Badge, Button, Card, cell, ErrorNote, inputClass, Loading, Table } from "@/components/ui";
import { useApi } from "@/lib/api";
import { label, when } from "@/lib/format";
import { RULES, type ExceptionRow, type Page } from "@/lib/types";

const PAGE_SIZE = 50;

function ExceptionList() {
  const router = useRouter();
  const params = useSearchParams();
  const filters = {
    status: params.get("status") ?? "open",
    rule: params.get("rule") ?? "",
    severity: params.get("severity") ?? "",
    hub: params.get("hub") ?? "",
  };
  const offset = Number(params.get("offset") ?? 0);

  const query = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
  for (const [key, value] of Object.entries(filters)) if (value) query.set(key, value);
  const { data, error, loading } = useApi<Page<ExceptionRow>>(`/exceptions?${query}`);

  function update(changes: Record<string, string>) {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    if (!("offset" in changes)) next.delete("offset");
    router.replace(`/exceptions?${next}`);
  }

  const select = (name: keyof typeof filters, options: string[], allLabel: string) => (
    <label className="text-sm">
      <span className="block text-xs text-slate-500">{name}</span>
      <select className={inputClass} value={filters[name]} onChange={(e) => update({ [name]: e.target.value })}>
        <option value="">{allLabel}</option>
        {options.map((o) => (
          <option key={o} value={o}>
            {label(o)}
          </option>
        ))}
      </select>
    </label>
  );

  return (
    <Card>
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        {select("status", ["open", "resolved"], "any status")}
        {select("rule", [...RULES], "any rule")}
        {select("severity", ["high", "medium", "low"], "any severity")}
        <label className="text-sm">
          <span className="block text-xs text-slate-500">hub</span>
          <input
            className={inputClass}
            placeholder="e.g. BLR-GW"
            defaultValue={filters.hub}
            onKeyDown={(e) => e.key === "Enter" && update({ hub: e.currentTarget.value.trim().toUpperCase() })}
          />
        </label>
      </div>
      <ErrorNote message={error} />
      {loading && !data ? (
        <Loading />
      ) : data && data.items.length === 0 ? (
        <p className="text-sm text-slate-500">No exceptions match.</p>
      ) : (
        data && (
          <>
            <Table head={["severity", "rule", "shipment", "hub", "detected", "status", "pending"]}>
              {data.items.map((x) => (
                <tr key={x.id} className="hover:bg-slate-50 dark:hover:bg-slate-800/50">
                  <td className={cell}>
                    <Badge value={x.severity} />
                  </td>
                  <td className={cell}>
                    <Link className="font-medium hover:underline" href={`/exceptions/${x.id}`}>
                      {label(x.rule)}
                    </Link>
                  </td>
                  <td className={`${cell} font-mono text-xs`}>{x.tracking_number}</td>
                  <td className={cell}>{x.hub ?? "–"}</td>
                  <td className={`${cell} whitespace-nowrap`}>{when(x.detected_at)}</td>
                  <td className={cell}>
                    <Badge value={x.status} />
                  </td>
                  <td className={`${cell} tabular-nums`}>{x.pending_actions || ""}</td>
                </tr>
              ))}
            </Table>
            <div className="mt-3 flex items-center justify-between text-sm text-slate-500">
              <span>
                {offset + 1}–{offset + data.items.length} of {data.total}
              </span>
              <span className="flex gap-2">
                <Button variant="secondary" disabled={offset === 0} onClick={() => update({ offset: String(Math.max(0, offset - PAGE_SIZE)) })}>
                  Previous
                </Button>
                <Button
                  variant="secondary"
                  disabled={offset + PAGE_SIZE >= data.total}
                  onClick={() => update({ offset: String(offset + PAGE_SIZE) })}
                >
                  Next
                </Button>
              </span>
            </div>
          </>
        )
      )}
    </Card>
  );
}

export default function Page() {
  return (
    <Shell title="Exceptions">
      <Suspense fallback={<Loading />}>
        <ExceptionList />
      </Suspense>
    </Shell>
  );
}
