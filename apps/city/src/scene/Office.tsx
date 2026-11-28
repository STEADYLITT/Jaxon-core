// Inside a floor: one desk per agent, a low-poly robot at each, and a glowing monitor that
// shows that agent's live feed (current task, last 8 log lines, tool calls, time on task).
import { Edges, Html, MeshReflectorMaterial } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
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
  const share = useCity((st) => st.shareToken);
  useEffect(() => {
    if (share) return; // client view: monitors show only the task from the scoped snapshot
    let live = true;
    const load = () => api<AgentDetail>(`/city/agents/${id}`).then((x) => { if (live) setD(x); })
      .catch(() => undefined);
    load();
    const t = window.setInterval(load, 3000);
    return () => { live = false; window.clearInterval(t); };
  }, [id, last, share]);
  return d;
}

/** A glossy low-poly robot with glowing eyes in its agent's status colour. The eyes only
 *  blink and the head only turns while the agent is really working. */
function Robot({ color, working }: { color: string; working: boolean }) {
  const head = useRef<THREE.Group>(null);
  const eyes = useRef<THREE.Group>(null);
  const glow = useMemo(() => new THREE.Color(color).multiplyScalar(3), [color]);
  useFrame(({ clock }) => {
    const t = clock.elapsedTime;
    if (head.current) head.current.rotation.y = working ? Math.sin(t * 2) * 0.25 : 0;
    if (eyes.current) eyes.current.scale.y = working && (t % 3.2) < 0.12 ? 0.15 : 1;
  });
  return (
    // beside its desk, turned to face whoever is looking in (so its eyes are seen)
    <group position={[2.85, 0, 0.7]} rotation-y={Math.PI + 0.45}>
      <mesh position-y={1.1}><cylinderGeometry args={[0.45, 0.55, 1.2, 6]} />
        <meshPhysicalMaterial color="#3b3290" metalness={0.7} roughness={0.25} clearcoat={1}
          clearcoatRoughness={0.1} emissive="#2a1f6a" emissiveIntensity={0.5} flatShading /></mesh>
      <mesh position-y={1.35}><torusGeometry args={[0.5, 0.03, 6, 24]} />
        <meshBasicMaterial color={glow} toneMapped={false} /></mesh>
      <group ref={head} position-y={2.05}>
        <mesh><boxGeometry args={[0.8, 0.6, 0.6]} />
          <meshPhysicalMaterial color="#5c4fc0" metalness={0.6} roughness={0.2} clearcoat={1}
            clearcoatRoughness={0.08} emissive="#3a2c8a" emissiveIntensity={0.4} flatShading /></mesh>
        <mesh position={[0, 0.02, -0.305]}><planeGeometry args={[0.66, 0.3]} />
          <meshBasicMaterial color="#05030f" side={THREE.DoubleSide} /></mesh>
        <group ref={eyes} position={[0, 0.03, -0.32]}>
          {[-0.15, 0.15].map((x) => (
            <mesh key={x} position-x={x}><sphereGeometry args={[0.075, 12, 8]} />
              <meshBasicMaterial color={glow} toneMapped={false} /></mesh>
          ))}
        </group>
        <mesh position={[0, 0.42, 0]}><cylinderGeometry args={[0.02, 0.02, 0.25, 6]} />
          <meshStandardMaterial color="#8b80e0" metalness={0.8} roughness={0.3} /></mesh>
        <mesh position={[0, 0.57, 0]}><sphereGeometry args={[0.06, 10, 8]} />
          <meshBasicMaterial color={glow} toneMapped={false} /></mesh>
      </group>
      <mesh position-y={0.25}><cylinderGeometry args={[0.6, 0.6, 0.12, 8]} />
        <meshStandardMaterial color="#1a1538" metalness={0.8} roughness={0.3} /></mesh>
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
      onClick={(e) => {
        e.stopPropagation();
        if (!useCity.getState().shareToken) setFocus({ kind: "agent", id: agent.id, from: focus });
      }}>
      {/* a glass desk on brushed metal legs, its edge lit in the agent's colour */}
      <mesh position-y={1.0}><boxGeometry args={[4.2, 0.1, 1.8]} />
        <meshPhysicalMaterial color="#8fa6ff" metalness={0.1} roughness={0.05} transmission={0.6}
          thickness={0.3} transparent opacity={0.55} clearcoat={1} clearcoatRoughness={0.05} />
        <Edges color={color} threshold={15} /></mesh>
      {[-1.9, 1.9].map((lx) => (
        <mesh key={lx} position={[lx, 0.5, 0]}><boxGeometry args={[0.1, 1, 1.5]} />
          <meshStandardMaterial color="#2a2650" metalness={0.9} roughness={0.25} /></mesh>
      ))}
      {/* the holographic monitor: a thin lit frame, a translucent pane and a scan glow */}
      <mesh position={[0, 2.3, -0.5]}><boxGeometry args={[3.4, 2.1, 0.04]} />
        <meshPhysicalMaterial color="#05030f" metalness={0.5} roughness={0.15} transparent opacity={0.7}
          clearcoat={1} />
        <Edges color={color} threshold={15} /></mesh>
      <mesh position={[0, 2.3, -0.46]}><planeGeometry args={[3.2, 1.9]} />
        <meshBasicMaterial color={color} transparent opacity={0.14} toneMapped={false}
          blending={THREE.AdditiveBlending} depthWrite={false} /></mesh>
      <mesh position={[0, 1.15, -0.5]}><boxGeometry args={[0.5, 0.2, 0.3]} />
        <meshBasicMaterial color={color} toneMapped={false} transparent opacity={0.5} /></mesh>
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

export function Office({ deptId, floorId, layer, light = false }: {
  deptId: string; floorId: string; layer: RefObject<HTMLDivElement | null>; light?: boolean;
}) {
  const s = useCity((st) => st.snapshot);
  const d = s?.departments.find((x) => x.id === deptId);
  const f = d?.floors.find((x) => x.id === floorId);
  if (!s || !d || !f) return null;
  const agents = f.agents.map((id) => s.agents.find((a) => a.id === id)).filter((a): a is Agent => !!a);
  const width = Math.max(agents.length * DESK_GAP + 6, 16);
  return (
    <group name="office">
      {/* a polished dark floor that mirrors the desks (plain on phones) */}
      <mesh rotation-x={-Math.PI / 2}><planeGeometry args={[width, 16]} />
        {light ? <meshStandardMaterial color="#0d0826" metalness={0.6} roughness={0.35} /> : (
          <MeshReflectorMaterial color="#0b0722" metalness={0.7} roughness={0.5} blur={[200, 60]}
            resolution={256} mixBlur={1} mixStrength={6} mirror={0.45} depthScale={0} />
        )}</mesh>
      <gridHelper args={[Math.max(width, 16), 16, "#f0b040", "#2a1a58"]} position-y={0.01}
        material-transparent material-opacity={0.22} />
      {/* back wall: smoked glass panels with the department's colour running along the top */}
      <mesh position={[0, 4, -5]}><planeGeometry args={[width, 8]} />
        <meshPhysicalMaterial color="#070414" metalness={0.2} roughness={0.25} clearcoat={1}
          envMapIntensity={0.15} emissive={d.color} emissiveIntensity={0.025} /></mesh>
      {Array.from({ length: Math.floor(width / 4) + 1 }, (_, i) => (
        <mesh key={i} position={[-width / 2 + i * 4, 4, -4.97]}><planeGeometry args={[0.05, 8]} />
          <meshBasicMaterial color="#3a2c7a" toneMapped={false} /></mesh>
      ))}
      <mesh position={[0, 7.6, -4.95]}><planeGeometry args={[width, 0.12]} />
        <meshBasicMaterial color={new THREE.Color(d.color).multiplyScalar(2.2)} toneMapped={false} /></mesh>
      <mesh position={[0, 0.06, -4.95]}><planeGeometry args={[width, 0.06]} />
        <meshBasicMaterial color={new THREE.Color("#f0b040").multiplyScalar(1.6)} toneMapped={false} /></mesh>
      <pointLight position={[0, 7, 3]} intensity={40} distance={30} color="#b9a8ff" />
      <pointLight position={[-width / 3, 3, 4]} intensity={14} distance={18} color="#f0b040" />
      <pointLight position={[width / 3, 3, 4]} intensity={14} distance={18} color={d.color} />
      {agents.map((a, i) => <Desk key={a.id} agent={a} x={(i - (agents.length - 1) / 2) * DESK_GAP} layer={layer} />)}
    </group>
  );
}
