// Inside a floor: one desk per agent, a low-poly robot at each, and a glowing monitor that
// shows that agent's live feed (current task, last 8 log lines, tool calls, time on task).
import { Html } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { useEffect, useRef, useState, type RefObject } from "react";
import * as THREE from "three";

import { api } from "../lib/api";
import { describe } from "../lib/effects";
import { windowColor } from "../lib/layout";
import { useCity } from "../lib/store";
import type { Agent, AgentDetail } from "../lib/types";

const DESK_GAP = 6;

function useDetail(id: string): AgentDetail | null {
  const [d, setD] = useState<AgentDetail | null>(null);
  const last = useCity((st) => st.lastEventId);
  useEffect(() => {
    let live = true;
    const load = () => api<AgentDetail>(`/city/agents/${id}`).then((x) => { if (live) setD(x); })
      .catch(() => undefined);
    load();
    const t = window.setInterval(load, 3000);
    return () => { live = false; window.clearInterval(t); };
  }, [id, last]);
  return d;
}

function Robot({ color, working }: { color: string; working: boolean }) {
  const head = useRef<THREE.Group>(null);
  useFrame(({ clock }) => {
    if (head.current) head.current.rotation.y = working ? Math.sin(clock.elapsedTime * 2) * 0.25 : 0;
  });
  return (
    <group position={[0, 0, 1.4]}>
      <mesh position-y={1.1}><cylinderGeometry args={[0.45, 0.55, 1.2, 6]} />
        <meshStandardMaterial color="#5a4cb0" emissive="#2a1f6a" emissiveIntensity={0.6} flatShading /></mesh>
      <group ref={head} position-y={2.05}>
        <mesh><boxGeometry args={[0.8, 0.6, 0.6]} /><meshStandardMaterial color="#7a6ad0" emissive="#3a2c8a" emissiveIntensity={0.6} flatShading /></mesh>
        <mesh position={[0, 0.05, -0.31]}><planeGeometry args={[0.55, 0.14]} />
          <meshBasicMaterial color={color} toneMapped={false} side={THREE.DoubleSide} /></mesh>
      </group>
      <mesh position-y={0.25}><cylinderGeometry args={[0.6, 0.6, 0.12, 8]} />
        <meshStandardMaterial color="#1a1538" /></mesh>
    </group>
  );
}

function Desk({ agent, x, layer }: { agent: Agent; x: number; layer: RefObject<HTMLDivElement | null> }) {
  const d = useDetail(agent.id);
  const setFocus = useCity((st) => st.setFocus);
  const focus = useCity((st) => st.focus);
  const color = windowColor(agent);
  const log = (d?.log ?? []).slice(0, 8).reverse();
  const tools = (d?.tools ?? []).slice(0, 3);
  return (
    <group position={[x, 0, 0]} name={`desk-${agent.id}`}
      onClick={(e) => { e.stopPropagation(); setFocus({ kind: "agent", id: agent.id, from: focus }); }}>
      <mesh position-y={1.0}><boxGeometry args={[4.2, 0.15, 1.8]} /><meshStandardMaterial color="#17123a" /></mesh>
      {[-1.9, 1.9].map((lx) => (
        <mesh key={lx} position={[lx, 0.5, 0]}><boxGeometry args={[0.15, 1, 1.6]} /><meshStandardMaterial color="#120e2c" /></mesh>
      ))}
      <mesh position={[0, 2.3, -0.5]}><boxGeometry args={[3.4, 2.1, 0.12]} /><meshStandardMaterial color="#05030f" /></mesh>
      <mesh position={[0, 2.3, -0.43]}><planeGeometry args={[3.2, 1.9]} />
        <meshBasicMaterial color={color} transparent opacity={0.18} toneMapped={false} /></mesh>
      <Html position={[0, 2.3, -0.4]} transform distanceFactor={4.2} portal={layer as RefObject<HTMLElement>}
        className="monitor" zIndexRange={[5, 0]} pointerEvents="none">
        <div className="screen" data-agent={agent.id} style={{ borderColor: color }}>
          <div className="who">{agent.name}</div>
          <div className="task">{agent.current_task ? agent.current_task.title : "no task"}
            {d?.time_on_task_s != null ? ` · ${d.time_on_task_s}s` : ""}</div>
          <ol className="lines">{log.map((e) => <li key={e.id}>{String(e.payload["line"] ?? "")}</li>)}</ol>
          {tools.length > 0 && <div className="tools">{tools.map((e) => describe(e)).join(" · ")}</div>}
        </div>
      </Html>
      <Robot color={color} working={agent.status === "working"} />
      <mesh position={[0, 3.9, 0.2]} rotation-x={-Math.PI / 2}>
        <circleGeometry args={[0.25, 16]} />
        <meshBasicMaterial color={color} toneMapped={false} />
      </mesh>
    </group>
  );
}

export function Office({ deptId, floorId, layer }: {
  deptId: string; floorId: string; layer: RefObject<HTMLDivElement | null>;
}) {
  const s = useCity((st) => st.snapshot);
  const d = s?.departments.find((x) => x.id === deptId);
  const f = d?.floors.find((x) => x.id === floorId);
  if (!s || !d || !f) return null;
  const agents = f.agents.map((id) => s.agents.find((a) => a.id === id)).filter((a): a is Agent => !!a);
  const width = Math.max(agents.length * DESK_GAP + 6, 16);
  return (
    <group name="office">
      <mesh rotation-x={-Math.PI / 2}><planeGeometry args={[width, 16]} /><meshStandardMaterial color="#0d0826" /></mesh>
      <gridHelper args={[Math.max(width, 16), 16, "#3a1d7a", "#1d1240"]} position-y={0.01} />
      <mesh position={[0, 4, -5]}><planeGeometry args={[width, 8]} />
        <meshStandardMaterial color="#0b0620" emissive={d.color} emissiveIntensity={0.08} /></mesh>
      <mesh position={[0, 7.6, -4.95]}><planeGeometry args={[width, 0.12]} />
        <meshBasicMaterial color={d.color} toneMapped={false} /></mesh>
      <pointLight position={[0, 7, 3]} intensity={40} distance={30} color="#b9a8ff" />
      {agents.map((a, i) => <Desk key={a.id} agent={a} x={(i - (agents.length - 1) / 2) * DESK_GAP} layer={layer} />)}
    </group>
  );
}
