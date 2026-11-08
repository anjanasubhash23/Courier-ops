"use client";

import Link from "next/link";

import { Shell } from "@/components/Shell";
import { Card, cell, ErrorNote, Loading, Table } from "@/components/ui";
import { useApi } from "@/lib/api";
import { count, label } from "@/lib/format";
import type { Hub } from "@/lib/types";

function Hubs() {
  const { data, error, loading } = useApi<Hub[]>("/hubs");
  if (loading && !data) return <Loading />;
  if (error || !data) return <ErrorNote message={error} />;
  return (
    <Card>
      <Table head={["hub", "name", "city", "type", "shipments here", "open exceptions"]}>
        {data.map((h) => (
          <tr key={h.code}>
            <td className={`${cell} font-mono text-xs`}>{h.code}</td>
            <td className={cell}>{h.name}</td>
            <td className={cell}>{h.city}</td>
            <td className={cell}>{label(h.hub_type)}</td>
            <td className={`${cell} tabular-nums`}>{count(h.shipments_here)}</td>
            <td className={`${cell} tabular-nums`}>
              {h.open_exceptions ? (
                <Link className="hover:underline" href={`/exceptions?hub=${h.code}`}>
                  {count(h.open_exceptions)}
                </Link>
              ) : (
                "–"
              )}
            </td>
          </tr>
        ))}
      </Table>
    </Card>
  );
}

export default function Page() {
  return (
    <Shell title="Hubs">
      <Hubs />
    </Shell>
  );
}
