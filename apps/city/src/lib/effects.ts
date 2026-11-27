// Turns a real event into what the City draws. No event, no animation.
import { CEO_TOP, anchor, roof, type Vec3 } from "./layout";
import type { CityEvent, Snapshot } from "./types";

export const MAX_ENVELOPES = 60;
export const ENVELOPE_MS = 1800;
export const BEAM_MS = 1600;

export const ENVELOPE_COLOR: Record<string, string> = {
  command: "#00e5ff", task: "#ff3df2", report: "#39ff88", failed: "#ff3355",
  approval: "#ff2244", decision: "#ffd166",
};

export interface Envelope {
  kind: "envelope"; eventId: number; from: Vec3; to: Vec3; color: string;
  sealed: boolean; start: number; label: string; runId?: string | null;
}
export interface Beam { kind: "beam"; eventId: number; at: Vec3; color: string; start: number }
export type Effect = Envelope | Beam;

export function effectsFor(ev: CityEvent, s: Snapshot, now: number): Effect[] {
  const env = (style: string, sealed = false): Envelope | null => {
    const from = anchor(s, ev.from?.kind, ev.from?.id, ev.agent_id);
    const to = anchor(s, ev.to?.kind, ev.to?.id, ev.agent_id);
    if (!from || !to) return null;
    return { kind: "envelope", eventId: ev.id, from, to, color: ENVELOPE_COLOR[style] ?? "#fff",
      sealed, start: now, label: ev.type, runId: ev.run_id };
  };
  const agent = s.agents.find((a) => a.id === ev.agent_id);
  // Beams rise from a department's roof; Jaxon's own tasks show as the orb's pulse.
  const beamAt = agent?.department_id ? roof(s, agent.department_id) : null;
  const beam = (color: string): Beam | null =>
    beamAt ? { kind: "beam", eventId: ev.id, at: beamAt, color, start: now } : null;
  const out: (Effect | null)[] = [];
  switch (ev.type) {
    case "command.issued": out.push(env("command")); break;
    case "task.created": out.push(env("task")); break;
    case "task.completed":
      out.push(env("report"));
      out.push(beam("#39ff88"));
      break;
    case "task.failed":
      out.push(env("failed"));
      out.push(beam("#ff3355"));
      break;
    case "approval.requested": out.push(env("approval", true)); break;
    case "alert.raised": out.push(env("approval", true)); break; // a red letter to Steady
    case "approval.decided": out.push(env("decision")); break;
    case "revenue.recorded":
      out.push({ kind: "beam", eventId: ev.id, at: CEO_TOP, color: "#ffd166", start: now });
      break;
    default: break; // logs, tool calls, heartbeats: the orb pulses, nothing flies
  }
  return out.filter((e): e is Effect => e !== null);
}

/** At most MAX_ENVELOPES fly at once; the rest wait their turn (FIFO). */
export function admit(active: Effect[], queue: Effect[], now: number): { active: Effect[]; queue: Effect[] } {
  const alive = active.filter((e) => now - e.start < (e.kind === "beam" ? BEAM_MS : ENVELOPE_MS));
  if (alive.length === active.length && !queue.length) return { active, queue };
  const q = [...queue];
  let flying = alive.filter((e) => e.kind === "envelope").length;
  while (q.length && (q[0]!.kind === "beam" || flying < MAX_ENVELOPES)) {
    const e = q.shift()!;
    if (e.kind === "envelope") flying += 1;
    alive.push({ ...e, start: now });
  }
  return { active: alive, queue: q };
}

export function describe(ev: CityEvent): string {
  const p = ev.payload as Record<string, unknown>;
  const t = (k: string) => (typeof p[k] === "string" ? (p[k] as string) : "");
  switch (ev.type) {
    case "command.issued": return `Steady → Jaxon: ${t("text")}`;
    case "command.routed": return `Jaxon → ${t("agent")}: ${t("reason")}`;
    case "task.created": return `Task: ${t("title")}`;
    case "task.started": return `Started: ${t("title")}`;
    case "task.completed": return `Done: ${t("title")}`;
    case "task.failed": return `Failed: ${t("title")} — ${t("error")}`;
    case "approval.requested": return `Needs approval: ${t("question") || t("title")}`;
    case "approval.decided": return `Steady ${t("decision")} an approval`;
    case "tool.called": return `Tool: ${t("tool")}${p["model"] ? ` (${t("model")})` : ""}`;
    case "agent.log": return t("line");
    case "cost.recorded": return `Cost $${Number(p["usd"] ?? 0).toFixed(4)}`;
    case "revenue.recorded": return `Revenue $${(Number(p["amount_cents"] ?? 0) / 100).toFixed(2)}`;
    case "agent.offline": return "Agent went offline";
    case "alert.raised": return `⚠ ${t("message")}`;
    case "agent.paused": return p["reason"] ? `Paused by the ${t("reason")}` : "Agent paused";
    case "agent.resumed": return "Agent resumed";
    case "agent.killed": return `Agent killed (${t("stopped")})`;
    case "agent.heartbeat": return p["back_online"] ? "Agent back online" : `Working: ${t("task")}`;
    default: return ev.type;
  }
}
