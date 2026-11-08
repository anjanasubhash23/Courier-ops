"use client";

import { useParams } from "next/navigation";
import { useState, type FormEvent } from "react";

import { ActionCard } from "@/components/ActionCard";
import { can, Shell, useUser } from "@/components/Shell";
import { Badge, Button, Card, ErrorNote, inputClass, Loading } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { label, when } from "@/lib/format";
import type { ExceptionDetail } from "@/lib/types";

// Parameters each action type takes; mirrors core/actions/policy.py.
const PARAMS: Record<string, { name: string; options?: string[]; placeholder?: string }[]> = {
  escalate_to_hub: [{ name: "note", placeholder: "What should the hub do?" }],
  reroute: [{ name: "via_hub", placeholder: "Hub code, e.g. BOM-GW" }],
  reschedule_delivery: [{ name: "slot", options: ["morning", "evening"] }],
  notify_customer: [{ name: "template", options: ["delay", "failed_attempt", "returning", "lost"] }],
};

function ProposeForm({ detail, onDone }: { detail: ExceptionDetail; onDone: () => void }) {
  const [actionType, setActionType] = useState(detail.allowed_actions[0] ?? "");
  const [params, setParams] = useState<Record<string, string>>({});
  const [rationale, setRationale] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // One key per proposal: a retried or double-sent submit returns the same action instead of a duplicate.
  const [idempotencyKey, setIdempotencyKey] = useState(() => crypto.randomUUID());

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const fields = PARAMS[actionType] ?? [];
      const body = Object.fromEntries(fields.map((f) => [f.name, params[f.name] ?? f.options?.[0] ?? ""]));
      await api(`/exceptions/${detail.id}/actions`, {
        method: "POST",
        json: { action_type: actionType, params: body, rationale: rationale || null },
        headers: { "Idempotency-Key": idempotencyKey },
      });
      setIdempotencyKey(crypto.randomUUID());
      setParams({});
      setRationale("");
      setError(null);
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-2">
      <select className={inputClass} value={actionType} onChange={(e) => setActionType(e.target.value)}>
        {detail.allowed_actions.map((a) => (
          <option key={a} value={a}>
            {label(a)}
          </option>
        ))}
      </select>
      {(PARAMS[actionType] ?? []).map((f) =>
        f.options ? (
          <select
            key={f.name}
            className={inputClass}
            value={params[f.name] ?? f.options[0]}
            onChange={(e) => setParams({ ...params, [f.name]: e.target.value })}
          >
            {f.options.map((o) => (
              <option key={o} value={o}>
                {label(o)}
              </option>
            ))}
          </select>
        ) : (
          <input
            key={f.name}
            className={inputClass}
            placeholder={f.placeholder}
            value={params[f.name] ?? ""}
            onChange={(e) => setParams({ ...params, [f.name]: e.target.value })}
            required
          />
        ),
      )}
      <input className={inputClass} placeholder="Why? (optional)" value={rationale} onChange={(e) => setRationale(e.target.value)} />
      <ErrorNote message={error} />
      <Button type="submit" disabled={busy || !actionType}>
        Propose
      </Button>
      <p className="text-xs text-slate-500">A supervisor other than you has to approve it before anything happens.</p>
    </form>
  );
}

function ExceptionView({ id }: { id: string }) {
  const user = useUser();
  const { data, error, loading, reload } = useApi<ExceptionDetail>(`/exceptions/${id}`);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [resolveNote, setResolveNote] = useState("");

  async function post(path: string, json?: unknown) {
    setBusy(true);
    try {
      await api(path, { method: "POST", json });
      setActionError(null);
      await reload();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (loading && !data) return <Loading />;
  if (error || !data) return <ErrorNote message={error} />;
  const s = data.shipment;
  const triage = data.triage.at(-1);
  const open = data.status === "open";

  return (
    <>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Badge value={data.severity} />
        <Badge value={data.status} />
        <span className="font-medium">{label(data.rule)}</span>
        <span className="text-slate-500">
          at {data.hub ?? "unknown hub"} · detected {when(data.detected_at)} · last seen {when(data.last_seen_at)}
          {data.resolved_by && ` · resolved by ${data.resolved_by} ${when(data.resolved_at)}`}
        </span>
      </div>
      <ErrorNote message={actionError} />

      <div className="grid gap-4 md:grid-cols-[1fr_1.3fr]">
        <div className="space-y-4">
          <Card title={<span className="font-mono">{s.tracking_number}</span>}>
            <dl className="grid grid-cols-[8rem_1fr] gap-y-1 text-sm">
              <dt className="text-slate-500">Route</dt>
              <dd>
                {s.origin_hub} → {s.destination_hub}
              </dd>
              <dt className="text-slate-500">Service</dt>
              <dd>
                {label(s.service_level)}, {(s.weight_grams / 1000).toFixed(1)} kg
              </dd>
              <dt className="text-slate-500">Status</dt>
              <dd>
                {label(s.status)}
                {s.last_scan_hub && ` at ${s.last_scan_hub}`}
              </dd>
              <dt className="text-slate-500">Booked</dt>
              <dd>{when(s.booked_at)}</dd>
              <dt className="text-slate-500">Promised by</dt>
              <dd>{when(s.promised_by)}</dd>
              {s.delivered_at && (
                <>
                  <dt className="text-slate-500">Delivered</dt>
                  <dd>{when(s.delivered_at)}</dd>
                </>
              )}
            </dl>
          </Card>
          <Card title="Timeline">
            <ol className="space-y-1.5 text-sm">
              {data.timeline.map((e, i) => (
                <li key={i} className="grid grid-cols-[6.5rem_1fr] gap-2">
                  <span className="whitespace-nowrap text-xs text-slate-500">{when(e.occurred_at)}</span>
                  <span>
                    {label(e.event_type)}
                    {e.hub && <span className="text-slate-500"> · {e.hub}</span>}
                    {e.reason && <span className="text-slate-500"> · {label(e.reason)}</span>}
                  </span>
                </li>
              ))}
            </ol>
          </Card>
        </div>

        <div className="space-y-4">
          <Card
            title="Triage"
            actions={
              open && can(user, "operator") ? (
                <Button variant="secondary" disabled={busy} onClick={() => post(`/exceptions/${id}/triage`)}>
                  {busy ? "Working…" : triage ? "Run again" : "Run triage"}
                </Button>
              ) : null
            }
          >
            {triage ? (
              <div className="space-y-2 text-sm">
                <p>
                  <span className="font-medium">{label(triage.cause)}</span>{" "}
                  <span className="text-slate-500">
                    · {Math.round(triage.confidence * 100)}% confident · {triage.diagnoser}
                  </span>
                </p>
                <p>{triage.summary}</p>
                <ul className="list-disc space-y-0.5 pl-5 text-slate-600 dark:text-slate-400">
                  {triage.evidence.map((ev, i) => (
                    <li key={i}>{ev}</li>
                  ))}
                </ul>
              </div>
            ) : (
              <p className="text-sm text-slate-500">Not triaged yet. Triage proposes actions; it never runs them.</p>
            )}
          </Card>

          <Card title="Actions">
            <div className="space-y-2">
              {data.actions.length === 0 && <p className="text-sm text-slate-500">None yet.</p>}
              {data.actions.map((a) => (
                <ActionCard key={a.id} action={a} onChange={() => void reload()} />
              ))}
            </div>
          </Card>

          {open && can(user, "operator") && (
            <>
              <Card title="Propose an action">
                <ProposeForm detail={data} onDone={() => void reload()} />
              </Card>
              <Card title="Resolve by hand">
                <div className="flex gap-2">
                  <input
                    className={inputClass}
                    placeholder="What fixed it?"
                    value={resolveNote}
                    onChange={(e) => setResolveNote(e.target.value)}
                  />
                  <Button variant="secondary" disabled={busy || !resolveNote.trim()} onClick={() => post(`/exceptions/${id}/resolve`, { note: resolveNote })}>
                    Resolve
                  </Button>
                </div>
              </Card>
            </>
          )}
        </div>
      </div>
    </>
  );
}

export default function Page() {
  const { id } = useParams<{ id: string }>();
  return (
    <Shell title={`Exception #${id}`}>
      <ExceptionView id={id} />
    </Shell>
  );
}
