import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { parseSse } from "../src/lib/api";
import { MAX_ENVELOPES, admit, describe as describeEvent, effectsFor, type Effect } from "../src/lib/effects";
import { CEO_TOP, ORB, STATUS_COLOR, anchor, buildingPos, windowColor, windows } from "../src/lib/layout";
import { seeded } from "../src/lib/rand";
import { useCity } from "../src/lib/store";
import { agent, ev, snapshot } from "./fixtures";

describe("windows", () => {
  it("one window per real agent, only in staffed buildings", () => {
    const w = windows(snapshot(), 1);
    expect(w.map((x) => x.agentId)).toEqual(["a1", "a2"]); // Jaxon's agent lives in the orb
    const all = windows(snapshot());
    expect(new Set(all.map((x) => x.agentId))).toEqual(new Set(["a1", "a2"])); // same 2 lights
    expect(all).toHaveLength(8); // seen from every side
  });
  it("colors follow the spec", () => {
    const now = Date.parse("2026-11-27T12:00:00Z");
    expect(windowColor(agent({ status: "working" }), now)).toBe(STATUS_COLOR.working);
    expect(windowColor(agent({ status: "waiting" }), now)).toBe(STATUS_COLOR.waiting);
    expect(windowColor(agent({ status: "failed" }), now)).toBe(STATUS_COLOR.failed);
    expect(windowColor(agent({ status: "working", online: false }), now)).toBe(STATUS_COLOR.offline);
    expect(windowColor(agent({ status: "done", status_changed_at: "2026-11-27T11:59:00Z" }), now))
      .toBe(STATUS_COLOR.just_done);
    expect(windowColor(agent({ status: "done", status_changed_at: "2026-11-27T11:00:00Z" }), now))
      .toBe(STATUS_COLOR.done);
    expect(windowColor(agent({ status: "idle" }), now)).toBe(STATUS_COLOR.idle); // dark = truth
  });
});

describe("envelopes and beams come only from events", () => {
  const s = snapshot();
  it("routes letters between the CEO tower, Jaxon and buildings", () => {
    const cmd = effectsFor(ev(11, "command.issued", { from: { kind: "ceo", id: "steady" },
      to: { kind: "jaxon", id: "jaxon" } }), s, 0);
    expect(cmd).toHaveLength(1);
    expect(cmd[0]).toMatchObject({ kind: "envelope", from: CEO_TOP, to: ORB });
    const appr = effectsFor(ev(12, "approval.requested", { agent_id: "a1",
      from: { kind: "department", id: "d-mkt" }, to: { kind: "ceo", id: "steady" } }), s, 0);
    expect(appr[0]).toMatchObject({ sealed: true, to: CEO_TOP });
    const done = effectsFor(ev(13, "task.completed", { agent_id: "a1",
      from: { kind: "department", id: "d-mkt" }, to: { kind: "jaxon", id: "jaxon" } }), s, 0);
    expect(done.map((e) => e.kind)).toEqual(["envelope", "beam"]);
    expect(done[1]).toMatchObject({ color: "#39ff88" });
    expect(effectsFor(ev(14, "revenue.recorded"), s, 0)[0]).toMatchObject({ kind: "beam", color: "#ffd166" });
  });
  it("routing reads as Jaxon's decision with its reason", () => {
    const e = ev(30, "command.routed", { payload: { agent: "Content Writer", reason: "the command mentions 'post'" } });
    expect(describeEvent(e)).toBe("Jaxon → Content Writer: the command mentions 'post'");
    expect(effectsFor(e, s, 0)).toEqual([]); // the task's own letter does the flying
  });
  it("logs and heartbeats only pulse the orb", () => {
    for (const t of ["agent.log", "tool.called", "agent.heartbeat", "cost.recorded"]) {
      expect(effectsFor(ev(20, t), s, 0)).toEqual([]);
    }
  });
  it("a department letter lands on the agent's floor", () => {
    const floor0 = anchor(s, "department", "d-mkt", "a1")!;
    const roof = anchor(s, "department", "d-mkt", null)!;
    expect(floor0[1]).toBeLessThan(roof[1]);
    const [bx, , bz] = buildingPos(s.departments[0]!);
    expect(Math.hypot(floor0[0], floor0[2])).toBeLessThan(Math.hypot(bx, bz));
  });
  it("caps flying envelopes at 60 and queues the rest", () => {
    const many: Effect[] = Array.from({ length: 75 }, (_, i) => ({ kind: "envelope", eventId: i,
      from: CEO_TOP, to: ORB, color: "#fff", sealed: false, start: 0, label: "x" }));
    const r = admit([], many, 0);
    expect(r.active).toHaveLength(MAX_ENVELOPES);
    expect(r.queue).toHaveLength(15);
    const later = admit(r.active, r.queue, 10_000); // first batch landed
    expect(later.active).toHaveLength(15);
    expect(later.queue).toHaveLength(0);
  });
});

describe("store", () => {
  it("ticker keeps the last 20 real events, newest first, no duplicates", () => {
    useCity.setState({ snapshot: snapshot(), ticker: [], queue: [], active: [], lastEventId: 0 });
    for (let i = 1; i <= 25; i++) useCity.getState().onEvent(ev(i, "agent.log"), i);
    useCity.getState().onEvent(ev(25, "agent.log"), 26);
    const t = useCity.getState().ticker;
    expect(t).toHaveLength(20);
    expect(t[0]!.id).toBe(25);
    expect(useCity.getState().lastEventId).toBe(25);
  });
});

describe("stream cursor", () => {
  it("only streamed events move it; a refetch never skips events", () => {
    useCity.setState({ snapshot: null, ticker: [], queue: [], active: [], lastEventId: 0 });
    useCity.getState().setSnapshot(snapshot({ last_event_id: 10 }));
    expect(useCity.getState().lastEventId).toBe(10);
    useCity.getState().setSnapshot(snapshot({ last_event_id: 15 })); // refetch, events 11-15 not streamed yet
    expect(useCity.getState().lastEventId).toBe(10);
    useCity.getState().onEvent(ev(11, "task.created", { from: { kind: "jaxon", id: "jaxon" },
      to: { kind: "department", id: "d-mkt" }, agent_id: "a1" }), 0);
    expect(useCity.getState().lastEventId).toBe(11);
    expect(useCity.getState().queue).toHaveLength(1);
  });
});

describe("sse", () => {
  it("parses complete events and keeps the partial tail", () => {
    const e = ev(5, "task.started");
    const { events, rest } = parseSse(`retry: 2000\n\nid: 5\nevent: task.started\ndata: ${JSON.stringify(e)}\n\nid: 6\ndata: {"id"`);
    expect(events.map((x) => x.id)).toEqual([5]);
    expect(rest).toBe('id: 6\ndata: {"id"');
  });
});

describe("rule zero", () => {
  it("our code never uses Math.random, faker or mock activity", () => {
    const files: string[] = [];
    const walk = (d: string) => readdirSync(d).forEach((f: string) => {
      const p = join(d, f);
      if (statSync(p).isDirectory()) walk(p); else if (/\.(ts|tsx)$/.test(p)) files.push(p);
    });
    walk(join(dirname(fileURLToPath(import.meta.url)), "../src"));
    for (const f of files) {
      const src = readFileSync(f, "utf8");
      expect(src, f).not.toMatch(/Math\.random|faker|setInterval\([^)]*onEvent/);
    }
  });
  it("decoration uses a seeded generator: the same stars every time", () => {
    const a = seeded(7), b = seeded(7);
    expect([a(), a(), a()]).toEqual([b(), b(), b()]);
  });
});

describe("replay", () => {
  it("rebuilds statuses from events in order, and finds the moment", async () => {
    const { statusesAt, indexAt } = await import("../src/lib/replay");
    const evs = [
      ev(1, "task.started", { agent_id: "a1", ts: "2026-11-27T10:00:00Z" }),
      ev(2, "approval.requested", { agent_id: "a1", ts: "2026-11-27T10:01:00Z" }),
      ev(3, "task.completed", { agent_id: "a1", ts: "2026-11-27T10:05:00Z" }),
      ev(4, "agent.offline", { agent_id: "a2", ts: "2026-11-27T10:06:00Z" }),
    ];
    expect(statusesAt(evs, 0).get("a1")?.status).toBe("working");
    expect(statusesAt(evs, 1).get("a1")?.status).toBe("waiting");
    expect(statusesAt(evs, 3).get("a1")).toEqual({ status: "done", at: "2026-11-27T10:05:00Z" });
    expect(statusesAt(evs, 3).get("a2")?.status).toBe("offline");
    expect(indexAt(evs, Date.parse("2026-11-27T10:02:00Z"))).toBe(1);
    expect(indexAt(evs, Date.parse("2026-11-27T09:00:00Z"))).toBe(-1);
  });
  it("alerts fly as red sealed letters to the CEO tower", () => {
    const e = ev(40, "alert.raised", { from: { kind: "department", id: "d-mkt" },
      to: { kind: "ceo", id: "steady" }, agent_id: "a1", payload: { message: "over limit" } });
    expect(effectsFor(e, snapshot(), 0)[0]).toMatchObject({ sealed: true, to: CEO_TOP });
    expect(describeEvent(e)).toBe("⚠ over limit");
  });
});
