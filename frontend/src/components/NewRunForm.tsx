"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { api } from "@/lib/api";
import type { AgentSpec } from "@/lib/types";
import { Card, ErrorNote } from "./ui";

export function NewRunForm({ agents }: { agents: AgentSpec[] }) {
  const router = useRouter();
  const [agent, setAgent] = useState("general");
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!input.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const run = await api.createRun(agent, input.trim(), "queue");
      router.push(`/runs/${run.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  return (
    <Card title="New run">
      <form onSubmit={submit} className="space-y-3">
        <label className="block text-sm">
          <span className="mb-1 block text-zinc-500">Agent</span>
          <select
            value={agent}
            onChange={(e) => setAgent(e.target.value)}
            className="w-full rounded-md border border-zinc-300 bg-white px-2 py-1.5 dark:border-zinc-700 dark:bg-zinc-800"
          >
            {agents.map((a) => (
              <option key={a.name} value={a.name}>
                {a.name} — {a.description}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-sm">
          <span className="mb-1 block text-zinc-500">Task</span>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            rows={4}
            placeholder="e.g. Estimate monthly revenue for 40 clients at $1,250/month with 8% churn."
            className="w-full rounded-md border border-zinc-300 bg-white px-2 py-1.5 dark:border-zinc-700 dark:bg-zinc-800"
          />
        </label>
        {error && <ErrorNote message={error} />}
        <button
          type="submit"
          disabled={busy || !input.trim()}
          className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900"
        >
          {busy ? "Queuing…" : "Run agent"}
        </button>
      </form>
    </Card>
  );
}
