"use client";

import { api } from "@/lib/api";
import { Badge, Card, Dot, ErrorNote } from "./ui";
import { usePoll } from "./usePoll";

export function SystemPanel() {
  const ready = usePoll(api.ready, 5000);
  const system = usePoll(api.system, 10000);

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Card
        title="Health"
        action={ready.data && <Badge status={ready.data.status} label={ready.data.status.replace("_", " ")} />}
      >
        {ready.error && <ErrorNote message={ready.error} />}
        <ul className="space-y-1.5 text-sm">
          {ready.data &&
            Object.entries(ready.data.checks).map(([name, c]) => (
              <li key={name} className="flex items-center gap-2">
                <Dot ok={c.ok} />
                <span className="font-medium">{name}</span>
                <span className="truncate text-zinc-500">
                  {String(c.version ?? c.revision ?? c.provider ?? c.error ?? "")}
                </span>
              </li>
            ))}
        </ul>
      </Card>
      <Card title="System">
        {system.error && <ErrorNote message={system.error} />}
        {system.data && (
          <div className="space-y-3 text-sm">
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1">
              <dt className="text-zinc-500">Version</dt>
              <dd>{system.data.version}</dd>
              <dt className="text-zinc-500">Environment</dt>
              <dd>{system.data.environment}</dd>
              <dt className="text-zinc-500">Model</dt>
              <dd className="font-mono text-xs">
                {system.data.model_provider} / {system.data.default_model}
              </dd>
              <dt className="text-zinc-500">Skills</dt>
              <dd>{system.data.skills}</dd>
              <dt className="text-zinc-500">Queue depth</dt>
              <dd>{system.data.queue_depth ?? "unavailable"}</dd>
            </dl>
            <div>
              <h3 className="mb-1 text-xs font-semibold uppercase text-zinc-500">Integrations</h3>
              <ul className="space-y-1">
                {system.data.integrations.map((i) => (
                  <li key={i.name} className="flex items-center gap-2">
                    <Dot ok={i.configured} />
                    <span className="font-medium">{i.name}</span>
                    <span className="text-xs text-zinc-500">
                      {i.configured ? "credentials set (not verified)" : `missing ${i.required_env.join(", ")}`}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}
