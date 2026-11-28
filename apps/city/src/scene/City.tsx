import { Html, OrbitControls, PerformanceMonitor } from "@react-three/drei";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { createContext, useContext, useMemo, useRef, useState, type RefObject } from "react";
import * as THREE from "three";

import { BEAM_MS, ENVELOPE_MS, type Beam, type Envelope } from "../lib/effects";
import {
  BUILDING_W, FLOOR_H, ORB, ORB_Y, RING_R, TOWER_H, TOWER_W, buildingHeight, buildingPos, facing,
  windowColor, windows, type Vec3,
} from "../lib/layout";
import type { Mode } from "../lib/mode";
import { seeded } from "../lib/rand";
import { statusesAt, type PastStatus } from "../lib/replay";
import { useCity, type Focus } from "../lib/store";
import { CeoTower, HoloLot, JaxonOrb, Tower as DeptTower, crownH } from "./Architecture";
import { Office } from "./Office";
import { HAZE, Post, World } from "./World";
import type { Department, Snapshot } from "../lib/types";

// Labels portal into a stable overlay outside the canvas (drei's default target changes
// when the canvas connects its events, which can drop an early label's content).
const LabelLayer = createContext<RefObject<HTMLDivElement | null> | null>(null);

function Label({ position, className, children }: {
  position: [number, number, number]; className: string; children: React.ReactNode;
}) {
  const layer = useContext(LabelLayer);
  return (
    <Html position={position} center className={className} pointerEvents="none"
      portal={layer as RefObject<HTMLElement> | undefined}>{children}</Html>
  );
}

type Pose = { pos: Vec3; target: Vec3; key: string };

/** How far back the overview camera stands: portrait phones need more room for the ring. */
function farFor(aspect: number): number {
  return aspect < 0.8 ? 1.95 : aspect < 1.2 ? 1.4 : 1;
}

/** Fog that starts behind the ring at whatever distance the camera stands. */
function AutoFog() {
  const { size } = useThree();
  const far = farFor(size.width / Math.max(size.height, 1));
  // the far ground melts into a dusk purple that sits under the nebula's horizon band
  return <fog attach="fog" args={[HAZE, 110 * far, 430 * far]} />;
}

/** Where the camera should be for what Steady is looking at. Portrait phones need the
 *  camera further back to fit the ring. */
function poseFor(focus: Focus, s: Snapshot, aspect: number, inOffice: boolean): Pose | null {
  const far = farFor(aspect);
  if (inOffice) return { pos: [0, 7 * far, 15 * far], target: [0, 2.2, 0], key: "office" };
  // low enough that the horizon (and the galaxy above it) is in the shot
  if (!focus) return { pos: [0, 44 * far, 92 * far], target: [0, 9, 0], key: "city" };
  if (focus.kind === "building") {
    const d = s.departments.find((x) => x.id === focus.id);
    if (!d) return null;
    const [x, , z] = buildingPos(d, s.departments.length);
    const h = buildingHeight(d);
    return { pos: [x * 1.9, h + 12 * far, z * 1.9], target: [x, h / 2, z], key: `b${d.id}` };
  }
  if (focus.kind === "jaxon") return { pos: [0, ORB_Y + 4, 24 * far], target: ORB, key: "jaxon" };
  if (focus.kind === "ceo") return { pos: [0, TOWER_H + 6, 20 * far], target: [0, TOWER_H - 2, 0], key: "ceo" };
  return null; // an agent panel: stay where we are
}

function CameraRig({ inOffice }: { inOffice: boolean }) {
  const { camera, size, controls } = useThree();
  const focus = useCity((st) => st.focus);
  const s = useCity((st) => st.snapshot);
  const pose = s ? poseFor(focus, s, size.width / Math.max(size.height, 1), inOffice) : null;
  const flying = useRef<Pose | null>(null);
  const lastKey = useRef("");
  if (pose && pose.key !== lastKey.current) {
    lastKey.current = pose.key;
    flying.current = pose;
  }
  useFrame((_, dt) => {
    const p = flying.current;
    const c = controls as unknown as { target: THREE.Vector3; update(): void } | null;
    if (!p) return;
    const k = 1 - Math.exp(-dt * 4);
    camera.position.lerp(new THREE.Vector3(...p.pos), k);
    if (c) { c.target.lerp(new THREE.Vector3(...p.target), k); c.update(); }
    else camera.lookAt(...p.target);
    if (camera.position.distanceTo(new THREE.Vector3(...p.pos)) < 0.05) flying.current = null;
  });
  return null;
}

/** For the acceptance tests: where a named object is on screen, so a test can click it
 *  with the real mouse (and the real raycaster). Reads only; changes nothing. */
function ScreenProbe() {
  const { scene, camera, size } = useThree();
  useFrame(() => {
    (window as unknown as { __cityScreenOf?: (n: string) => [number, number] | null }).__cityScreenOf =
      (name: string) => {
        const o = scene.getObjectByName(name);
        if (!o) return null;
        const box = new THREE.Box3().setFromObject(o);
        const v = box.getCenter(new THREE.Vector3()).project(camera);
        return [(v.x + 1) / 2 * size.width, (1 - v.y) / 2 * size.height];
      };
  });
  return null;
}

/** A red glass shell that pulses only while the building's real failure siren sounds. */
function Siren({ id, h }: { id: string; h: number }) {
  const mat = useRef<THREE.MeshBasicMaterial>(null);
  useFrame(({ clock }) => {
    const on = (useCity.getState().snapshot?.sirens ?? []).includes(id);
    if (!mat.current) return;
    mat.current.visible = on;
    if (on) mat.current.opacity = 0.12 + ((Math.sin(clock.elapsedTime * 8) + 1) / 2) * 0.3;
  });
  return (
    <mesh position-y={h / 2 + 0.3}>
      <boxGeometry args={[BUILDING_W + 0.25, h + 0.2, BUILDING_W + 0.25]} />
      <meshBasicMaterial ref={mat} color="#ff2244" transparent opacity={0} visible={false}
        toneMapped={false} depthWrite={false} />
    </mesh>
  );
}

function pointer(on: boolean) {
  document.body.style.cursor = on ? "pointer" : "";
}

function RingRoad() {
  return (
    <group name="ring-road">
      <mesh rotation-x={-Math.PI / 2} position-y={0.02}>
        <ringGeometry args={[RING_R - 7.5, RING_R - 6.2, 96]} />
        <meshBasicMaterial color="#ff3df2" transparent opacity={0.55} toneMapped={false} />
      </mesh>
    </group>
  );
}

function Tower({ inbox }: { inbox: number }) {
  return (
    <group>
      <group name="ceo-tower"
        onClick={(e) => { e.stopPropagation(); useCity.getState().setFocus({ kind: "ceo" }); }}
        onPointerOver={() => pointer(true)} onPointerOut={() => pointer(false)}>
        <CeoTower />
      </group>
      {Array.from({ length: Math.min(inbox, 12) }, (_, i) => (
        <mesh key={i} position={[-1.6 + (i % 4) * 1.05, TOWER_H + 0.35 + Math.floor(i / 4) * 0.35, 0]}
          rotation-x={-Math.PI / 2} name="inbox-letter">
          <planeGeometry args={[0.9, 0.6]} />
          <meshBasicMaterial color={new THREE.Color("#ff2244").multiplyScalar(2.2)} toneMapped={false}
            side={THREE.DoubleSide} />
        </mesh>
      ))}
    </group>
  );
}

function CeoLabel({ inbox }: { inbox: number }) {
  return (
    <Label position={[0, TOWER_H + 4.6, 0]} className="label ceo">
      CEO · Steady{inbox ? <b className="inbox-count"> {inbox}</b> : null}
    </Label>
  );
}

function Orb() {
  return (
    <group position={ORB} onPointerOver={() => pointer(true)} onPointerOut={() => pointer(false)}>
      <JaxonOrb onClick={() => useCity.getState().setFocus({ kind: "jaxon" })} />
    </group>
  );
}

function Building({ d, n }: { d: Department; n: number }) {
  const [x, , z] = buildingPos(d, n);
  const rot = facing(d, n);
  if (!d.staffed) {
    return (
      <group position={[x, 0, z]} rotation-y={rot} name={`lot-${d.key}`}
        onClick={(e) => { e.stopPropagation(); useCity.getState().setFocus({ kind: "building", id: d.id }); }}
        onPointerOver={() => pointer(true)} onPointerOut={() => pointer(false)}>
        {/* the lot itself is clickable: its line's playbook, proposed agents and keys */}
        <HoloLot color={d.color} />
        <Label position={[0, 5.2, 0]} className="label lot">{d.name}<br />not staffed</Label>
      </group>
    );
  }
  const h = buildingHeight(d);
  return (
    <group position={[x, 0, z]} rotation-y={rot} name={`building-${d.key}`}
      onClick={(e) => { e.stopPropagation(); useCity.getState().setFocus({ kind: "building", id: d.id }); }}
      onPointerOver={() => pointer(true)} onPointerOut={() => pointer(false)}>
      <DeptTower h={h} color={d.color} slot={d.slot} />
      <Siren id={d.id} h={h} />
      <Label position={[0, h + crownH(d.slot) + 1.6, 0]} className="label holo">{d.name}</Label>
      {d.floors.map((f, i) => i === 0 ? null : ( // a thin light band between floors
        <mesh key={f.id} position-y={i * FLOOR_H + 0.3}>
          <boxGeometry args={[BUILDING_W + 0.06, 0.07, BUILDING_W + 0.06]} />
          <meshBasicMaterial color={new THREE.Color(d.color).multiplyScalar(1.3)} toneMapped={false} />
        </mesh>
      ))}
    </group>
  );
}

function Windows({ s }: { s: Snapshot }) {
  const spots = useMemo(() => windows(s), [s]);
  const ref = useRef<THREE.InstancedMesh>(null);
  const tmp = useMemo(() => new THREE.Object3D(), []);
  const col = useMemo(() => new THREE.Color(), []);
  const byId = useMemo(() => new Map(s.agents.map((a) => [a.id, a])), [s]);
  const past = useRef<{ index: number; map: Map<string, PastStatus> } | null>(null);
  useFrame(({ clock }) => {
    const m = ref.current;
    if (!m) return;
    const t = clock.elapsedTime;
    const r = useCity.getState().replay;
    let now = Date.now();
    let map: Map<string, PastStatus> | null = null;
    if (r) {  // replay: statuses rebuilt from the events up to the replay clock
      if (!past.current || past.current.index !== r.index) {
        past.current = { index: r.index, map: statusesAt(r.events, r.index) };
      }
      map = past.current.map;
      now = r.clock;
    }
    spots.forEach((w, i) => {
      tmp.position.set(...w.pos);
      tmp.rotation.set(0, w.rotY, 0);
      tmp.updateMatrix();
      m.setMatrixAt(i, tmp.matrix);
      let a = byId.get(w.agentId);
      if (a && map) {
        const p = map.get(a.id);
        a = { ...a, status: p?.status ?? "idle", online: p?.status !== "offline",
              status_changed_at: p?.at ?? null };
      }
      col.set(a ? windowColor(a, now) : "#000");
      if (a) { // active states glow (they bloom); offline/idle stay dim. Only online agents flicker.
        const st = a.online || a.status === "paused" ? a.status : "offline";
        const gain = st === "working" || st === "waiting" || st === "failed" ? 2.6
          : st === "done" ? 1.6 : st === "offline" ? 0.7 : 2.2; // idle = online, lit softly
        const flick = a.online ? 0.92 + 0.08 * Math.sin(t * 3.1 + i * 1.7) : 1;
        col.multiplyScalar(gain * flick);
      }
      m.setColorAt(i, col);
    });
    m.instanceMatrix.needsUpdate = true;
    if (m.instanceColor) m.instanceColor.needsUpdate = true;
  });
  if (!spots.length) return null;
  return (
    <instancedMesh ref={ref} args={[undefined, undefined, spots.length]} name="windows"
      onClick={(e) => {
        e.stopPropagation();
        const w = e.instanceId !== undefined ? spots[e.instanceId] : undefined;
        if (w) useCity.getState().setFocus({ kind: "agent", id: w.agentId, from: useCity.getState().focus });
      }}>
      <planeGeometry args={[1, 1.2]} />
      <meshBasicMaterial toneMapped={false} side={THREE.DoubleSide} />
    </instancedMesh>
  );
}

function EnvelopeMesh({ e }: { e: Envelope }) {
  const ref = useRef<THREE.Mesh>(null);
  const { camera } = useThree();
  const curve = useMemo(() => {
    const a = new THREE.Vector3(...e.from), b = new THREE.Vector3(...e.to);
    const mid = a.clone().lerp(b, 0.5);
    mid.y = Math.max(a.y, b.y) + 2 + a.distanceTo(b) * 0.06; // stays in frame
    return new THREE.QuadraticBezierCurve3(a, mid, b);
  }, [e]);
  useFrame(() => {
    if (!ref.current) return;
    const t = Math.min((performance.now() - e.start) / ENVELOPE_MS, 1);
    ref.current.position.copy(curve.getPoint(t));
    ref.current.quaternion.copy(camera.quaternion); // always face the viewer
  });
  return (
    <mesh ref={ref} name="envelope" position={e.from}
      onClick={(ev) => { ev.stopPropagation(); if (e.runId) useCity.getState().setFocus({ kind: "trace", runId: e.runId }); }}
      onPointerOver={() => pointer(!!e.runId)} onPointerOut={() => pointer(false)}>
      <planeGeometry args={[2.4, 1.6]} />
      <meshBasicMaterial color={e.color} toneMapped={false} side={THREE.DoubleSide} />
      {e.sealed && (
        <mesh position-z={0.02}>
          <circleGeometry args={[0.4, 20]} />
          <meshBasicMaterial color="#8b0010" toneMapped={false} />
        </mesh>
      )}
    </mesh>
  );
}

const BEAM_H = 26;

function BeamMesh({ b }: { b: Beam }) {
  const group = useRef<THREE.Group>(null);
  const mat = useRef<THREE.MeshBasicMaterial>(null);
  useFrame(() => {
    const t = Math.min((performance.now() - b.start) / BEAM_MS, 1);
    if (group.current) group.current.scale.y = Math.min(0.05 + t * 3, 1); // shoots up fast
    if (mat.current) mat.current.opacity = t < 0.5 ? 0.95 : 0.95 * (1 - (t - 0.5) * 2);
  });
  // rises from the roof: green success, red fail, gold revenue
  return (
    <group ref={group} position={b.at} name="beam">
      <mesh position-y={BEAM_H / 2}>
        <cylinderGeometry args={[0.45, 0.8, BEAM_H, 14, 1, true]} />
        <meshBasicMaterial ref={mat} color={b.color} transparent opacity={0.95}
          toneMapped={false} side={THREE.DoubleSide} depthWrite={false} />
      </mesh>
    </group>
  );
}

function Effects() {
  const active = useCity((st) => st.active);
  useFrame(() => useCity.getState().step(performance.now()));
  return (
    <group>
      {active.map((e) => e.kind === "envelope"
        ? <EnvelopeMesh key={`e${e.eventId}-${e.label}`} e={e} />
        : <BeamMesh key={`b${e.eventId}`} b={e} />)}
    </group>
  );
}

export default function City({ mode }: { mode: Mode }) {
  const s = useCity((st) => st.snapshot);
  const focus = useCity((st) => st.focus);
  const layer = useRef<HTMLDivElement>(null);
  const floor = focus?.kind === "floor" ? focus
    : focus?.kind === "agent" && focus.from?.kind === "floor" ? focus.from : null;
  // A slow GPU drops to the light look on its own (no bloom, 1x pixels).
  const [slow, setSlow] = useState(false);
  const light = mode === "low" || slow;
  if (!s) return null;
  const n = s.departments.length;
  return (
    <>
    <div ref={layer} className="label-layer" />
    <LabelLayer.Provider value={layer}>
    <Canvas dpr={light ? 1 : [1, 2]} camera={{ position: [0, 38, 62], fov: 50, far: 1400 }}
      gl={{ antialias: mode !== "low", powerPreference: "high-performance" }}
      onCreated={() => { document.documentElement.dataset.cityRender = light ? "light" : "full"; }}>
      <color attach="background" args={["#07031a"]} />
      <AutoFog />
      <ambientLight intensity={0.22} />
      <pointLight position={ORB} intensity={60} color="#9b5cff" distance={80} />
      <PerformanceMonitor onDecline={() => setSlow(true)} flipflops={1} />
      <CameraRig inOffice={!!floor} />
      <ScreenProbe />
      <World light={light} />
      {floor ? <Office deptId={floor.deptId} floorId={floor.floorId} layer={layer} /> : (
        <group name="city" onPointerMissed={() => useCity.getState().setFocus(null)}>
          <RingRoad />
          <Tower inbox={s.inbox.approvals.length} />
          <CeoLabel inbox={s.inbox.approvals.length} />
          <Orb />
          {s.departments.map((d) => <Building key={d.id} d={d} n={n} />)}
          <Windows s={s} />
          <Effects />
        </group>
      )}
      <OrbitControls makeDefault target={[0, 13, 0]} enablePan={false} minDistance={20} maxDistance={260}
        maxPolarAngle={Math.PI / 2.1} />
      {!light && <Post />}
    </Canvas>
    </LabelLayer.Provider>
    </>
  );
}
