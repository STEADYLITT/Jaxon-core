import { OrbitControls, PerformanceMonitor } from "@react-three/drei";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";

import {
  BUILDING_W, FLOOR_H, ORB, ORB_Y, RING_R, TOWER_H, TOWER_W, buildingHeight, buildingPos, facing,
  windowColor, windows, type Vec3,
} from "../lib/layout";
import type { Mode } from "../lib/mode";
import { seeded } from "../lib/rand";
import { statusesAt, type PastStatus } from "../lib/replay";
import { useCity, type Focus } from "../lib/store";
import { slowFactor } from "../lib/effects";
import { BeamColumn, CoinBurst, EnvelopeFlight, RingRoad } from "./Life";
import { CeoTower, HoloLot, JaxonOrb, Tower as DeptTower, crownH } from "./Architecture";
import { Office } from "./Office";
import { HAZE, Post, World } from "./World";
import type { Department, Snapshot } from "../lib/types";

import { Label, LabelDeclutter, LabelLayer } from "./labels";

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

const easeInOut = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
const INTRO_MS = 3600;
const FLY_MS = 1500;
const IDLE_DRIFT_MS = 12000;

/** Whether the opening fly-in plays: not for reduced motion, and not under automation unless
 *  a test asks for it with ?intro (so every other test starts at the overview, as before). */
function introWanted(): boolean {
  const q = new URLSearchParams(window.location.search);
  if (q.has("intro")) return true;
  if (q.has("nointro") || navigator.webdriver) return false;
  return !window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

function driftWanted(): boolean {
  const q = new URLSearchParams(window.location.search);
  if (q.has("drift")) return true;
  return !navigator.webdriver && !window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

type Tween = { from: THREE.Vector3; to: THREE.Vector3; tFrom: THREE.Vector3; tTo: THREE.Vector3;
  via: THREE.Vector3 | null; start: number; ms: number };

/** The camera: a cinematic fly-in from deep space on first load (any key, click, touch or
 *  wheel skips it), eased fixed-length flights between views, and a slow orbit drift when
 *  Steady has left the overview alone for a while. Scenery motion only, never data. */
function CameraRig({ inOffice }: { inOffice: boolean }) {
  const { camera, size, controls } = useThree();
  const focus = useCity((st) => st.focus);
  const s = useCity((st) => st.snapshot);
  const pose = s ? poseFor(focus, s, size.width / Math.max(size.height, 1), inOffice) : null;
  const tween = useRef<Tween | null>(null);
  const lastKey = useRef("");
  const introDone = useRef(false);
  const lastInput = useRef(performance.now());

  const begin = (p: Pose, ms: number, via: THREE.Vector3 | null, from?: THREE.Vector3) => {
    const c = controls as unknown as { target: THREE.Vector3 } | null;
    tween.current = {
      from: (from ?? camera.position).clone(), to: new THREE.Vector3(...p.pos),
      tFrom: c ? c.target.clone() : new THREE.Vector3(...p.target), tTo: new THREE.Vector3(...p.target),
      via, start: performance.now(), ms,
    };
  };

  if (pose && pose.key !== lastKey.current) {
    const first = lastKey.current === "";
    lastKey.current = pose.key;
    if (first && !introDone.current && introWanted()) {
      // from deep space, through the nebula band, swinging round onto the city
      const far = farFor(size.width / Math.max(size.height, 1));
      const startAt = new THREE.Vector3(-140 * far, 300 * far, 430 * far);
      camera.position.copy(startAt);
      begin(pose, INTRO_MS * slowFactor(), new THREE.Vector3(120 * far, 120 * far, 260 * far), startAt);
      document.documentElement.dataset.cityIntro = "playing";
    } else {
      if (!introDone.current) document.documentElement.dataset.cityIntro = "off";
      introDone.current = true;
      begin(pose, first ? 1 : FLY_MS, null);
    }
  }

  useEffect(() => {
    const touch = () => {
      lastInput.current = performance.now();
      const t = tween.current;
      if (t && t.ms === INTRO_MS * slowFactor()) { // skip: finish the fly-in in a quick beat
        const pose = { pos: t.to.toArray() as Vec3, target: t.tTo.toArray() as Vec3, key: "skip" };
        begin(pose, 350, null);
      }
    };
    const evs = ["pointerdown", "wheel", "keydown", "touchstart"] as const;
    evs.forEach((e) => window.addEventListener(e, touch, { passive: true }));
    return () => evs.forEach((e) => window.removeEventListener(e, touch));
  });

  const drift = useMemo(driftWanted, []);
  useFrame(() => {
    const c = controls as unknown as { target: THREE.Vector3; update(): void; autoRotate: boolean;
      autoRotateSpeed: number } | null;
    const t = tween.current;
    (window as unknown as { __cityCamBusy?: boolean }).__cityCamBusy = !!t; // tests: settled?
    if (c) {
      const idle = performance.now() - lastInput.current > IDLE_DRIFT_MS;
      c.autoRotate = drift && !t && !focus && !inOffice && idle;
      c.autoRotateSpeed = 0.25;
    }
    if (!t) { if (c?.autoRotate) c.update(); return; }
    const k = Math.min((performance.now() - t.start) / t.ms, 1);
    const e = easeInOut(k);
    if (t.via) { // a quadratic arc through `via`
      const a = t.from.clone().lerp(t.via, e), b = t.via.clone().lerp(t.to, e);
      camera.position.copy(a.lerp(b, e));
    } else camera.position.lerpVectors(t.from, t.to, e);
    const target = t.tFrom.clone().lerp(t.tTo, e);
    if (c) { c.target.copy(target); c.update(); } else camera.lookAt(target);
    if (k >= 1) {
      tween.current = null;
      if (!introDone.current) {
        introDone.current = true;
        document.documentElement.dataset.cityIntro = "done";
      }
      lastInput.current = performance.now();
    }
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
    (window as unknown as { __cityCamera?: () => number[] }).__cityCamera = () => camera.position.toArray();
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

function Effects({ light }: { light: boolean }) {
  const active = useCity((st) => st.active);
  useFrame(() => useCity.getState().step(performance.now()));
  return (
    <group>
      {active.map((e) => e.kind === "envelope"
        ? <EnvelopeFlight key={`e${e.eventId}-${e.label}`} e={e} light={light} />
        : (
          <group key={`b${e.eventId}`}>
            <BeamColumn b={e} />
            {e.color === "#ffd166" && !light && <CoinBurst b={e} />}
          </group>
        ))}
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
  // Adaptive resolution: PerformanceMonitor's factor (0..1) scales the pixel ratio inside a
  // band, never above the screen's own. Desktop aims for 60 fps, phones (light) for 30.
  const [factor, setFactor] = useState(0.5);
  const maxDpr = typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1;
  const dpr = Math.min(maxDpr, Math.round((light ? 0.75 + 0.75 * factor : 1 + factor) * 4) / 4);
  useEffect(() => {
    document.documentElement.dataset.cityRender = light ? "light" : "full";
    document.documentElement.dataset.cityDpr = String(dpr);
  }, [light, dpr]);
  if (!s) return null;
  const n = s.departments.length;
  return (
    <>
    <div ref={layer} className="label-layer" />
    <LabelLayer.Provider value={layer}>
    <Canvas dpr={dpr} camera={{ position: [0, 38, 62], fov: 50, far: 1400 }}
      gl={{ antialias: mode !== "low", powerPreference: "high-performance" }}>
      <color attach="background" args={["#07031a"]} />
      <AutoFog />
      <ambientLight intensity={0.22} />
      <pointLight position={ORB} intensity={60} color="#9b5cff" distance={80} />
      <PerformanceMonitor bounds={(hz) => (light ? [26, Math.min(hz, 45)] : [Math.min(hz, 60) * 0.83, Math.min(hz, 60)])}
        onChange={({ factor: f }) => setFactor(f)} onDecline={() => { if (!light) setSlow(true); }}
        flipflops={3} />
      <CameraRig inOffice={!!floor} />
      <ScreenProbe />
      <LabelDeclutter />
      <World light={light} />
      {floor ? <Office deptId={floor.deptId} floorId={floor.floorId} layer={layer} light={light} /> : (
        <group name="city" onPointerMissed={() => useCity.getState().setFocus(null)}>
          <RingRoad light={light} />
          <Tower inbox={s.inbox.approvals.length} />
          <CeoLabel inbox={s.inbox.approvals.length} />
          <Orb />
          {s.departments.map((d) => <Building key={d.id} d={d} n={n} />)}
          <Windows s={s} />
          <Effects light={light} />
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
