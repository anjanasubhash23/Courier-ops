"use client";

import Link from "next/link";
import { useState } from "react";

import { can, useUser } from "@/components/Shell";
import { Badge, Button, ErrorNote, inputClass } from "@/components/ui";
import { api } from "@/lib/api";
import { label, when } from "@/lib/format";
import type { Action } from "@/lib/types";

/** One action, with approve/reject for supervisors who didn't propose it. */
export function ActionCard({ action, onChange, showException = false }: {
  action: Action;
  onChange: (updated: Action) => void;
  showException?: boolean;
}) {
  const user = useUser();
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const mayDecide = action.status === "proposed" && can(user, "supervisor") && action.proposed_by !== user.username;

  async function decide(decision: "approve" | "reject") {
    setBusy(true);
    try {
      onChange(await api<Action>(`/actions/${action.id}/${decision}`, { method: "POST", json: { note: note || null } }));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const params = Object.entries(action.params);
  return (
    <div className="space-y-2 rounded border border-slate-200 p-3 text-sm dark:border-slate-800">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{label(action.action_type)}</span>
        <Badge value={action.status} />
        {showException && (
          <Link className="text-xs text-slate-500 underline" href={`/exceptions/${action.exception_id}`}>
            exception #{action.exception_id}
          </Link>
        )}
        <span className="ml-auto text-xs text-slate-500">
          proposed by {action.proposed_by} · {when(action.proposed_at)}
        </span>
      </div>
      {params.length > 0 && (
        <p className="text-xs text-slate-600 dark:text-slate-400">
          {params.map(([k, v]) => `${label(k)}: ${v}`).join(" · ")}
        </p>
      )}
      {action.rationale && <p className="text-slate-600 dark:text-slate-400">{action.rationale}</p>}
      {action.decided_by && (
        <p className="text-xs text-slate-500">
          {action.status === "rejected" ? "Rejected" : "Approved"} by {action.decided_by}
          {action.decision_note ? `: “${action.decision_note}”` : ""}
        </p>
      )}
      {action.result && (
        <p className="rounded bg-slate-50 p-2 text-xs dark:bg-slate-800">
          {String(action.result.message ?? action.result.error ?? JSON.stringify(action.result))}
        </p>
      )}
      {mayDecide && (
        <div className="flex flex-wrap gap-2">
          <input className={`${inputClass} max-w-xs`} placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
          <Button onClick={() => decide("approve")} disabled={busy}>
            Approve and run
          </Button>
          <Button variant="secondary" onClick={() => decide("reject")} disabled={busy}>
            Reject
          </Button>
        </div>
      )}
      {action.status === "proposed" && action.proposed_by === user.username && (
        <p className="text-xs text-slate-500">Waiting for another supervisor: you can&apos;t approve your own proposal.</p>
      )}
      <ErrorNote message={error} />
    </div>
  );
}
