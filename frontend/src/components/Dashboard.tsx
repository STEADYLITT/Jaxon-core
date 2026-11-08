"use client";

import Link from "next/link";
import { api } from "@/lib/api";
import { durationMs, formatDuration, truncate } from "@/lib/format";
import { NewRunForm } from "./NewRunForm";
import { SystemPanel } from "./SystemPanel";
import { Badge, Card, ErrorNote } from "./ui";
import { usePoll } from "./usePoll";

export function Dashboard() {
  const agents = usePoll(api.agents, 30000);
  const skills = usePoll(api.skills, 30000);
  const runs = usePoll(() => api.runs(20), 4000);

  return (
    <div className="space-y-4">
      <SystemPanel />
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-1">
          {agents.data ? (
            <NewRunForm agents={agents.data} />
          ) : (
            <Card title="New run">{agents.error ? <ErrorNote message={agents.error} /> : "Loading…"}</Card>
          )}
        </div>
        <div className="lg:col-span-2">
          <Card title={`Recent runs${runs.data ? ` (${runs.data.total})` : ""}`}>
            {runs.error && <ErrorNote message={runs.error} />}
            {runs.data && runs.data.items.length === 0 && (
              <p className="text-sm text-zinc-500">No runs yet.</p>
            )}
            <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
              {runs.data?.items.map((r) => (
                <li key={r.id}>
                  <Link
                    href={`/runs/${r.id}`}
                    className="flex items-center gap-3 py-2 text-sm hover:bg-zinc-50 dark:hover:bg-zinc-800/50"
                  >
                    <Badge status={r.status} />
                    <span className="w-24 shrink-0 font-medium">{r.agent_name}</span>
                    <span className="flex-1 truncate text-zinc-600 dark:text-zinc-400">
                      {truncate(r.input, 90)}
                    </span>
                    <span className="shrink-0 text-xs text-zinc-500">
                      {formatDuration(durationMs(r.started_at, r.finished_at))}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          </Card>
        </div>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <Card title="Agents">
          <ul className="space-y-2 text-sm">
            {agents.data?.map((a) => (
              <li key={a.name}>
                <div className="flex items-center gap-2">
                  <span className="font-medium">{a.name}</span>
                  {a.builtin && <Badge status="builtin" />}
                </div>
                <p className="text-zinc-500">{a.description}</p>
                <p className="font-mono text-xs text-zinc-400">{a.skills.join(", ")}</p>
              </li>
            ))}
          </ul>
        </Card>
        <Card title={`Skills${skills.data ? ` (${skills.data.length})` : ""}`}>
          {skills.error && <ErrorNote message={skills.error} />}
          <ul className="space-y-2 text-sm">
            {skills.data?.map((s) => (
              <li key={s.name}>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-xs font-semibold">{s.name}</span>
                  {s.permissions.map((p) => (
                    <Badge key={p} status="perm" label={p} />
                  ))}
                  {s.requires_approval && <Badge status="failed" label="needs approval" />}
                </div>
                <p className="text-zinc-500">{s.description}</p>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </div>
  );
}
