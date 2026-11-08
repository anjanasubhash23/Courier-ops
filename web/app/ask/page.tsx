"use client";

import { useState, type FormEvent } from "react";

import { Shell } from "@/components/Shell";
import { Button, Card, cell, ErrorNote, inputClass, Table } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import type { Answer } from "@/lib/types";

const SUGGESTIONS = [
  "Which hub has the most open exceptions?",
  "What percentage of express shipments were delivered on time?",
  "How many failed delivery attempts were there for each reason?",
];

function Ask() {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e?: FormEvent, q = question) {
    e?.preventDefault();
    if (!q.trim()) return;
    setBusy(true);
    setError(null);
    try {
      setAnswer(await api<Answer>("/ask", { method: "POST", json: { question: q } }));
    } catch (err) {
      setAnswer(null);
      setError(
        err instanceof ApiError && err.status === 503
          ? "No LLM is configured on the server. Set LLM_PROVIDER and LLM_MODEL in .env (see .env.example)."
          : err instanceof Error
            ? err.message
            : String(err),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Card>
        <form onSubmit={submit} className="flex gap-2">
          <input
            className={inputClass}
            placeholder="Ask about shipments, hubs, exceptions or actions"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            maxLength={500}
          />
          <Button type="submit" disabled={busy}>
            {busy ? "Thinking…" : "Ask"}
          </Button>
        </form>
        <div className="mt-2 flex flex-wrap gap-2 text-xs">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              className="rounded border border-slate-200 px-2 py-1 text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800"
              onClick={() => {
                setQuestion(s);
                void submit(undefined, s);
              }}
            >
              {s}
            </button>
          ))}
        </div>
        <p className="mt-3 text-xs text-slate-500">
          Queries are read-only and limited to reporting views; they run as a database role that can&apos;t see anything else.
        </p>
      </Card>
      <ErrorNote message={error} />
      {answer && (
        <Card title={answer.ok ? `${answer.rows.length}${answer.truncated ? "+" : ""} rows` : "No answer"} actions={<span className="text-xs text-slate-500">{answer.model}</span>}>
          <div className="space-y-3">
            {answer.note && <p className="text-sm text-slate-600 dark:text-slate-400">{answer.note}</p>}
            <ErrorNote message={answer.ok ? null : answer.error} />
            {answer.sql && <pre className="overflow-x-auto rounded bg-slate-100 p-2 text-xs dark:bg-slate-800">{answer.sql}</pre>}
            {answer.ok && (
              <Table head={answer.columns}>
                {answer.rows.map((row, i) => (
                  <tr key={i}>
                    {row.map((v, j) => (
                      <td key={j} className={`${cell} tabular-nums`}>
                        {v === null ? "–" : String(v)}
                      </td>
                    ))}
                  </tr>
                ))}
              </Table>
            )}
            {answer.attempts.length > 1 && (
              <details className="text-xs text-slate-500">
                <summary>{answer.attempts.length} attempts</summary>
                <ol className="mt-1 list-decimal space-y-1 pl-5">
                  {answer.attempts.map((a, i) => (
                    <li key={i}>
                      <code>{a.sql ?? "(no query)"}</code>
                      {a.error && <span className="text-red-700 dark:text-red-400"> → {a.error}</span>}
                    </li>
                  ))}
                </ol>
              </details>
            )}
          </div>
        </Card>
      )}
    </>
  );
}

export default function Page() {
  return (
    <Shell title="Ask">
      <Ask />
    </Shell>
  );
}
