"use client";

import { ActionCard } from "@/components/ActionCard";
import { Shell } from "@/components/Shell";
import { Card, ErrorNote, Loading } from "@/components/ui";
import { useApi } from "@/lib/api";
import type { Action } from "@/lib/types";

function Queue() {
  const { data, error, loading, setData } = useApi<Action[]>("/actions?status=proposed&limit=200");
  if (loading && !data) return <Loading />;
  if (error || !data) return <ErrorNote message={error} />;
  return (
    <Card title={`${data.length} awaiting approval`}>
      <div className="space-y-2">
        {data.length === 0 && <p className="text-sm text-slate-500">Nothing to approve.</p>}
        {data.map((a) => (
          <ActionCard
            key={a.id}
            action={a}
            showException
            onChange={(updated) => setData(data.map((x) => (x.id === updated.id ? updated : x)))}
          />
        ))}
      </div>
    </Card>
  );
}

export default function Page() {
  return (
    <Shell title="Approvals">
      <p className="text-sm text-slate-500">
        Every action, whether proposed by an operator or by triage, waits here until a supervisor other than its proposer
        approves it. Approving runs it.
      </p>
      <Queue />
    </Shell>
  );
}
