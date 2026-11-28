// Life in the City: everything here moves ONLY because a real event happened (Rule Zero).
// Envelopes ride courier drones with light trails, beams rise from roofs, revenue bursts
// into coins, and the ring road carries the real ticker as holographic text.
import { Trail } from "@react-three/drei";
import { useFrame, useThree } from "@react-three/fiber";
import { useMemo, useRef } from "react";
import * as THREE from "three";

import { beamMs, collapse, describeItem, envelopeMs, type Beam, type Envelope } from "../lib/effects";
import { RING_R } from "../lib/layout";
import { useCity } from "../lib/store";
import { Label } from "./labels";

const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

function bright(hex: string, k: number) {
  return new THREE.Color(hex).multiplyScalar(k);
}

/** A small low-poly courier drone: body, four glowing rotors, and the envelope it carries. */
function Drone({ color, sealed }: { color: string; sealed: boolean }) {
  const rotors = useRef<THREE.Group>(null);
  useFrame((_, dt) => { if (rotors.current) rotors.current.rotation.y += dt * 30; });
  const glow = useMemo(() => bright(color, 2.6), [color]);
  return (
    <group>
      <mesh>
        <octahedronGeometry args={[0.42, 0]} />
        <meshStandardMaterial color="#1a1c2e" metalness={0.8} roughness={0.3} emissive={color}
          emissiveIntensity={0.4} />
      </mesh>
      <group ref={rotors} position-y={0.18}>
        {[0, 1, 2, 3].map((i) => (
          <mesh key={i} position={[Math.cos(i * Math.PI / 2) * 0.62, 0, Math.sin(i * Math.PI / 2) * 0.62]}
            rotation-x={-Math.PI / 2}>
            <ringGeometry args={[0.12, 0.22, 12]} />
            <meshBasicMaterial color={glow} toneMapped={false} side={THREE.DoubleSide} />
          </mesh>
        ))}
      </group>
      {/* the envelope hangs below: a glowing card; approvals carry a red wax seal */}
      <group position-y={-0.75}>
        <mesh>
          <boxGeometry args={[1.5, 0.95, 0.06]} />
          <meshStandardMaterial color="#141018" emissive={color} emissiveIntensity={1.4}
            metalness={0.2} roughness={0.4} toneMapped={false} />
        </mesh>
        <mesh position-z={0.04}>
          <planeGeometry args={[1.3, 0.75]} />
          <meshBasicMaterial color={bright(color, 1.8)} toneMapped={false} transparent opacity={0.35} />
        </mesh>
        {sealed && (
          <mesh position-z={0.06}>
            <circleGeometry args={[0.26, 24]} />
            <meshBasicMaterial color={bright("#ff2244", 3.2)} toneMapped={false} />
          </mesh>
        )}
      </group>
    </group>
  );
}

export function EnvelopeFlight({ e, light }: { e: Envelope; light: boolean }) {
  const ref = useRef<THREE.Group>(null);
  const { camera } = useThree();
  const curve = useMemo(() => {
    const a = new THREE.Vector3(...e.from), b = new THREE.Vector3(...e.to);
    const mid = a.clone().lerp(b, 0.5);
    mid.y = Math.max(a.y, b.y) + 3 + a.distanceTo(b) * 0.08; // a visible arc
    return new THREE.QuadraticBezierCurve3(a, mid, b);
  }, [e]);
  const look = useMemo(() => new THREE.Vector3(), []);
  useFrame(() => {
    if (!ref.current) return;
    const t = ease(Math.min((performance.now() - e.start) / envelopeMs(), 1));
    ref.current.position.copy(curve.getPoint(t));
    look.copy(camera.position).setY(ref.current.position.y);
    ref.current.lookAt(look); // the card faces the viewer, the drone stays level
  });
  const trail = useMemo(() => bright(e.color, 2), [e.color]);
  const body = (
    <group ref={ref} position={e.from} name="envelope"
      onClick={(ev) => { ev.stopPropagation(); if (e.runId) useCity.getState().setFocus({ kind: "trace", runId: e.runId }); }}>
      <group scale={2.6}><Drone color={e.color} sealed={e.sealed} /></group>
    </group>
  );
  if (light) return body; // phones: no trail geometry
  return (
    <Trail width={2.6} length={6} color={trail} attenuation={(w) => w * w} decay={1.2}>
      {body}
    </Trail>
  );
}

/** Task done / failed: a light column from the roof with a shockwave ring at its base. */
export function BeamColumn({ b }: { b: Beam }) {
  const group = useRef<THREE.Group>(null);
  const core = useRef<THREE.MeshBasicMaterial>(null);
  const halo = useRef<THREE.MeshBasicMaterial>(null);
  const ring = useRef<THREE.Mesh>(null);
  const H = 30;
  const c = useMemo(() => bright(b.color, 3), [b.color]);
  useFrame(() => {
    const t = Math.min((performance.now() - b.start) / beamMs(), 1);
    const fade = t < 0.55 ? 1 : 1 - (t - 0.55) / 0.45;
    if (group.current) group.current.scale.y = Math.min(0.05 + t * 3.2, 1);
    if (core.current) core.current.opacity = 0.95 * fade;
    if (halo.current) halo.current.opacity = 0.4 * fade;
    if (ring.current) {
      ring.current.scale.setScalar(1 + t * 6);
      (ring.current.material as THREE.MeshBasicMaterial).opacity = 0.8 * (1 - t);
    }
  });
  return (
    <group position={b.at} name="beam">
      <group ref={group}>
        <mesh position-y={H / 2}>
          <cylinderGeometry args={[0.55, 0.9, H, 12, 1, true]} />
          <meshBasicMaterial ref={core} color={c} transparent toneMapped={false}
            side={THREE.DoubleSide} depthWrite={false} blending={THREE.AdditiveBlending} />
        </mesh>
        <mesh position-y={H / 2}>
          <cylinderGeometry args={[1.8, 3.0, H, 16, 1, true]} />
          <meshBasicMaterial ref={halo} color={c} transparent toneMapped={false}
            side={THREE.DoubleSide} depthWrite={false} blending={THREE.AdditiveBlending} />
        </mesh>
      </group>
      <mesh ref={ring} rotation-x={-Math.PI / 2} position-y={0.1}>
        <ringGeometry args={[1.6, 2.0, 48]} />
        <meshBasicMaterial color={c} transparent toneMapped={false} depthWrite={false}
          side={THREE.DoubleSide} />
      </mesh>
    </group>
  );
}

/** Revenue: the gold beam plus a burst of coins that rise and fall back. */
export function CoinBurst({ b }: { b: Beam }) {
  const ref = useRef<THREE.InstancedMesh>(null);
  const N = 36;
  const seeds = useMemo(() => Array.from({ length: N }, (_, i) => {
    const a = (i / N) * Math.PI * 2 * 3.7; // a fixed spiral, not random
    const r = 0.35 + ((i * 37) % 11) / 11;
    return { dx: Math.cos(a) * r, dz: Math.sin(a) * r, up: 9 + ((i * 53) % 7), spin: 4 + (i % 5) };
  }), []);
  const tmp = useMemo(() => new THREE.Object3D(), []);
  useFrame(() => {
    const m = ref.current;
    if (!m) return;
    const t = Math.min((performance.now() - b.start) / beamMs(), 1) * 1.6;
    seeds.forEach((s, i) => {
      tmp.position.set(s.dx * t * 4, s.up * t - 9.8 * t * t * 1.2, s.dz * t * 4);
      tmp.rotation.set(t * s.spin, t * s.spin * 0.7, 0);
      tmp.scale.setScalar(t < 1.45 ? 1 : 0.001);
      tmp.updateMatrix();
      m.setMatrixAt(i, tmp.matrix);
    });
    m.instanceMatrix.needsUpdate = true;
  });
  return (
    <group position={b.at} name="coins">
      <instancedMesh ref={ref} args={[undefined, undefined, N]}>
        <cylinderGeometry args={[0.28, 0.28, 0.06, 14]} />
        <meshStandardMaterial color="#f0b040" metalness={1} roughness={0.25}
          emissive="#f0b040" emissiveIntensity={0.9} toneMapped={false} />
      </instancedMesh>
    </group>
  );
}

/** The ring road: a glowing two-lane track. Its holographic text is the real ticker. */
export function RingRoad({ light }: { light: boolean }) {
  const flow = useRef<THREE.ShaderMaterial>(null);
  const uniforms = useMemo(() => ({ uTime: { value: 0 },
    uGold: { value: new THREE.Color("#f0b040") }, uCyan: { value: new THREE.Color("#00e5ff") } }), []);
  useFrame((_, dt) => { uniforms.uTime.value += dt; });
  return (
    <group name="ring-road">
      <mesh rotation-x={-Math.PI / 2} position-y={0.03}>
        <ringGeometry args={[RING_R - 8.2, RING_R - 5.4, 160, 1]} />
        <meshStandardMaterial color="#0c0b18" metalness={0.7} roughness={0.35} />
      </mesh>
      <mesh rotation-x={-Math.PI / 2} position-y={0.05}>
        <ringGeometry args={[RING_R - 8.2, RING_R - 5.4, 160, 1]} />
        <shaderMaterial ref={flow} transparent depthWrite={false} uniforms={uniforms}
          vertexShader={/* glsl */ `
            varying vec2 vP;
            void main() { vP = position.xy; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`}
          fragmentShader={/* glsl */ `
            uniform float uTime; uniform vec3 uGold, uCyan; varying vec2 vP;
            void main() {
              float r = length(vP);
              float a = atan(vP.y, vP.x);
              float inner = ${(RING_R - 8.2).toFixed(1)}, outer = ${(RING_R - 5.4).toFixed(1)};
              float u = (r - inner) / (outer - inner);
              float edge = smoothstep(0.08, 0.0, u) + smoothstep(0.92, 1.0, u);
              float mid = smoothstep(0.03, 0.0, abs(u - 0.5)) * step(0.5, fract(a * 24.0));
              float lane = smoothstep(0.5, 0.0, abs(fract(a * 6.0 - uTime * 0.05) - 0.5));
              vec3 col = uGold * edge * 2.2 + uCyan * mid * 1.2 + uCyan * lane * 0.12;
              gl_FragColor = vec4(col, max(max(edge, mid * 0.8), lane * 0.12));
            }`} />
      </mesh>
      {!light && <TickerRing />}
    </group>
  );
}

/** The three newest real events float over the ring road as holographic text. */
function TickerRing() {
  const ticker = useCity((st) => st.ticker);
  const replay = useCity((st) => st.replay);
  const items = collapse(replay
    ? replay.events.slice(Math.max(0, replay.index - 499), replay.index + 1).reverse() : ticker, 3);
  const group = useRef<THREE.Group>(null);
  useFrame((_, dt) => { if (group.current) group.current.rotation.y += dt * 0.03; });
  return (
    <group ref={group}>
      {items.map((it, i) => {
        const a = Math.PI / 2 + (i - 1) * 0.42; // in front of the default view
        const r = RING_R - 6.8;
        return (
          <Label key={it.e.id} position={[Math.cos(a) * r, 2.2, Math.sin(a) * r]}
            className="label holo ticker-holo">{describeItem(it).slice(0, 64)}</Label>
        );
      })}
    </group>
  );
}
