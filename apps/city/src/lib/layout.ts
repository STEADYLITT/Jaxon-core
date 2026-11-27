// Where everything stands. Pure functions so the tests can check them.
import type { Agent, AgentStatus, Department, Snapshot } from "./types";

export type Vec3 = [number, number, number];

export const RING_R = 36; // room for 15 departments
export const FLOOR_H = 3.2;
export const BUILDING_W = 6;
export const TOWER_H = 22;
export const TOWER_W = 6;
export const ORB_Y = 30;
export const CEO_TOP: Vec3 = [0, TOWER_H + 0.6, 0];
export const ORB: Vec3 = [0, ORB_Y, 0];

export const STATUS_COLOR: Record<AgentStatus | "just_done", string> = {
  working: "#00e5ff",
  just_done: "#39ff88",
  done: "#1f6b4a",
  waiting: "#ffb020",
  failed: "#ff3355",
  offline: "#4a4f5c",
  killed: "#4a4f5c",
  paused: "#8a6cff",
  idle: "#16324a",
};

const JUST_DONE_MS = 2 * 60 * 1000;

/** The window color the spec asks for: cyan working, green just finished, amber waiting
 *  for approval, red failed, gray offline. Idle windows stay dark. */
export function windowColor(a: Agent, now = Date.now()): string {
  if (!a.online && a.status !== "paused") return STATUS_COLOR.offline;
  if (a.status === "done") {
    const at = a.status_changed_at ? Date.parse(a.status_changed_at) : 0;
    return now - at <= JUST_DONE_MS ? STATUS_COLOR.just_done : STATUS_COLOR.done;
  }
  return STATUS_COLOR[a.status];
}

export function windowState(a: Agent, now = Date.now()): string {
  if (!a.online && a.status !== "paused") return "offline";
  if (a.status === "done") {
    const at = a.status_changed_at ? Date.parse(a.status_changed_at) : 0;
    return now - at <= JUST_DONE_MS ? "just_done" : "done";
  }
  return a.status;
}

// Offset by half a slot so no building hides straight behind the CEO tower in the
// default (front) view.
export function slotAngle(slot: number, count = 7): number {
  return ((slot + 0.5) / count) * Math.PI * 2 - Math.PI / 2;
}

export function buildingPos(d: Department, count = 7): Vec3 {
  const a = slotAngle(d.slot, count);
  return [Math.cos(a) * RING_R, 0, Math.sin(a) * RING_R];
}

export function buildingHeight(d: Department): number {
  return Math.max(d.floors.length, 1) * FLOOR_H + 1.2;
}

/** Rotation (around Y) that turns a building's front face toward the CEO tower. */
export function facing(d: Department, count = 7): number {
  const [x, , z] = buildingPos(d, count);
  return Math.atan2(-x, -z);
}

export interface WindowSpot { agentId: string; pos: Vec3; rotY: number }

/** One window per agent on its floor. The same window shows on all four faces so it is
 *  visible from any angle (it is still one agent, one light). */
export function windows(s: Snapshot, faces = 4): WindowSpot[] {
  const out: WindowSpot[] = [];
  const n = s.departments.length;
  for (const d of s.departments) {
    if (!d.staffed) continue;
    const [bx, , bz] = buildingPos(d, n);
    for (let face = 0; face < faces; face++) {
      const rot = facing(d, n) + (face * Math.PI) / 2;
      const fx = Math.sin(rot), fz = Math.cos(rot); // face normal
      const rx = Math.cos(rot), rz = -Math.sin(rot); // along the facade
      d.floors.forEach((f, i) => {
        const y = i * FLOOR_H + FLOOR_H * 0.55;
        const k = f.agents.length;
        f.agents.forEach((id, j) => {
          const off = (j - (k - 1) / 2) * 1.25;
          out.push({ agentId: id, rotY: rot, pos: [
            bx + fx * (BUILDING_W / 2 + 0.03) + rx * off, y,
            bz + fz * (BUILDING_W / 2 + 0.03) + rz * off,
          ] });
        });
      });
    }
  }
  return out;
}

/** Where an envelope addressed to (kind, id) goes. Departments: the agent's floor if we
 *  know the agent, else the roof. */
export function anchor(s: Snapshot, kind: string | undefined, id: string | undefined,
  agentId?: string | null): Vec3 | null {
  if (kind === "ceo") return CEO_TOP;
  if (kind === "jaxon") return ORB;
  if (kind === "department") {
    const d = s.departments.find((x) => x.id === id);
    if (!d) return null;
    const [x, , z] = buildingPos(d, s.departments.length);
    const ag = agentId ? s.agents.find((a) => a.id === agentId) : undefined;
    const fi = ag ? d.floors.findIndex((f) => f.id === ag.floor_id) : -1;
    const y = fi >= 0 ? fi * FLOOR_H + FLOOR_H * 0.6 : buildingHeight(d) + 0.3;
    return [x * 0.86, y, z * 0.86]; // the facade side that faces the tower
  }
  return null;
}

export function roof(s: Snapshot, departmentId: string | null | undefined): Vec3 | null {
  const d = s.departments.find((x) => x.id === departmentId);
  if (!d) return null;
  const [x, , z] = buildingPos(d, s.departments.length);
  return [x, buildingHeight(d), z];
}
