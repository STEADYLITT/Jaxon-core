"use client";

import Link from "next/link";
import { api } from "@/lib/api";
import { durationMs, formatDuration, isTerminal } from "@/lib/format";
import type { Run, RunStep } from "@/lib/types";
import { Badge, Card, ErrorNote } from "./ui";
import { usePoll } from "./usePoll";
import { useState } from "react";

function StepView({ step }: { step: RunStep }) {
  const p = step.payload as Record<string, unknown>;
  const label =
    step.kind === "model_response"
      ? `Model response (${String(p.stop_reason ?? "")})`
      : step.kind === "skill_call"
        ? `Call ${String(p.skill)}`
        : `Result ${String(p.skill)} ${p.ok ? "✓" : "✗"}`;
  return (
    <li className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-800">
      <div className="mb-1 flex items-center gap-2 text-sm">
        <span className="text-xs text-zinc-400">#{step.index}</span>
        <span className="font-medium">{label}</span>
      </div>
      {step.kind === "model_response" && typeof p.text === "string" && p.text && (
        <p className="whitespace-pre-wrap text-sm">{p.text}</p>
      )}
      {step.kind !== "model_response" && (
        <pre className="overflow-x-auto rounded bg-zinc-50 p-2 text-xs dark:bg-zinc-950">
          {JSON.stringify(step.kind === "skill_call" ? p.arguments : p.result, null, 2)}
        </pre>
      )}
    </li>
  );
}

export function RunDetail({ id }: { id: string }) {
  const [active, setActive] = useState(true);
  const { data: run, error } = usePoll<Run>(
    async () => {
      const r = await api.run(id);
      if (isTerminal(r.status)) setActive(false);
      return r;
    },
    2000,
    active,
  );

  return (
    <div className="space-y-4">
      <Link href="/" className="text-sm text-zinc-500 hover:underline">
        ← Dashboard
      </Link>
      {error && <ErrorNote message={error} />}
      {run && (
        <>
          <Card title={`Run · ${run.agent_name}`} action={<Badge status={run.status} />}>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm md:grid-cols-4">
              <dt className="text-zinc-500">Model</dt>
              <dd className="font-mono text-xs">{run.model ?? "—"}</dd>
              <dt className="text-zinc-500">Duration</dt>
              <dd>{formatDuration(durationMs(run.started_at, run.finished_at))}</dd>
              <dt className="text-zinc-500">Tokens in / out</dt>
              <dd>
                {run.usage.input_tokens ?? 0} / {run.usage.output_tokens ?? 0}
              </dd>
              <dt className="text-zinc-500">Stop reason</dt>
              <dd>{run.stop_reason ?? "—"}</dd>
            </dl>
            <h3 className="mt-4 text-xs font-semibold uppercase text-zinc-500">Input</h3>
            <p className="whitespace-pre-wrap text-sm">{run.input}</p>
            {run.output && (
              <>
                <h3 className="mt-4 text-xs font-semibold uppercase text-zinc-500">Output</h3>
                <p className="whitespace-pre-wrap text-sm">{run.output}</p>
              </>
            )}
            {run.error && (
              <div className="mt-4">
                <ErrorNote message={`${run.error.code}: ${run.error.message}`} />
              </div>
            )}
          </Card>
          <Card title={`Steps (${run.steps?.length ?? 0})`}>
            {!run.steps?.length && <p className="text-sm text-zinc-500">No steps recorded yet.</p>}
            <ol className="space-y-2">
              {run.steps?.map((s) => <StepView key={s.index} step={s} />)}
            </ol>
          </Card>
        </>
      )}
    </div>
  );
}
