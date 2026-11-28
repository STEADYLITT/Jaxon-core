// Real architecture for the City: glass towers with distinct silhouettes, a gold CEO tower,
// the Jaxon orb, and holographic empty lots. Heights and windows still come from real
// floors and agents (layout.ts); only the shape and the materials are design.
import { useFrame } from "@react-three/fiber";
import { useMemo, useRef } from "react";
import * as THREE from "three";

import { BUILDING_W, TOWER_H, TOWER_W } from "../lib/layout";
import { seeded } from "../lib/rand";
import { useCity } from "../lib/store";

export type Style = "setback" | "spire" | "helipad" | "twin" | "crown" | "antenna";
const STYLES: Style[] = ["setback", "spire", "helipad", "twin", "crown", "antenna"];

/** Each department keeps its silhouette: chosen from its slot, so it never changes. */
export function styleFor(slot: number): Style {
  return STYLES[slot % STYLES.length]!;
}

export function bright(hex: string, k: number): THREE.Color {
  return new THREE.Color(hex).multiplyScalar(k);
}

const glassCache = new Map<string, THREE.MeshPhysicalMaterial>();

/** Dark glass: clear-coated, slightly metallic, tinted by the department colour. One
 *  material per colour, shared by every block of that colour (fewer state changes). */
export function glassMaterial(color: string, gold = false): THREE.MeshPhysicalMaterial {
  const key = `${color}${gold ? "g" : ""}`;
  let m = glassCache.get(key);
  if (!m) {
    m = new THREE.MeshPhysicalMaterial({
      color: new THREE.Color("#0a0b1a").lerp(new THREE.Color(color), 0.12),
      metalness: gold ? 0.85 : 0.6, roughness: 0.1, clearcoat: 1, clearcoatRoughness: 0.08,
      reflectivity: 0.6, envMapIntensity: 1.6, emissive: new THREE.Color(color),
      emissiveIntensity: gold ? 0.05 : 0.035,
    });
    glassCache.set(key, m);
  }
  return m;
}

const boxCache = new Map<string, THREE.BoxGeometry>();
const edgeCache = new Map<string, THREE.EdgesGeometry>();
function box(w: number, h: number, d: number): THREE.BoxGeometry {
  const k = `${w}|${h}|${d}`;
  let g = boxCache.get(k);
  if (!g) { g = new THREE.BoxGeometry(w, h, d); boxCache.set(k, g); }
  return g;
}
function edges(w: number, h: number, d: number): THREE.EdgesGeometry {
  const k = `${w}|${h}|${d}`;
  let g = edgeCache.get(k);
  if (!g) { g = new THREE.EdgesGeometry(box(w, h, d)); edgeCache.set(k, g); }
  return g;
}
const lineCache = new Map<string, THREE.LineBasicMaterial>();
function lineMaterial(c: THREE.Color): THREE.LineBasicMaterial {
  const k = c.getHexString() + c.r.toFixed(2);
  let m = lineCache.get(k);
  if (!m) { m = new THREE.LineBasicMaterial({ color: c, toneMapped: false }); lineCache.set(k, m); }
  return m;
}

/** Glowing edges of a box, in the department colour (they bloom; the glass doesn't). */
export function Edges({ w, h, d, color, k = 2.2, y = 0 }: {
  w: number; h: number; d: number; color: string | THREE.Color; k?: number; y?: number;
}) {
  const mat = useMemo(() => lineMaterial(typeof color === "string" ? bright(color, k) : color),
    [color, k]);
  return <lineSegments geometry={edges(w, h, d)} material={mat} position-y={y} />;
}

function Block({ w, h, y, color, gold }: { w: number; h: number; y: number; color: string; gold?: boolean }) {
  return (
    <group position-y={y}>
      <mesh position-y={h / 2} geometry={box(w, h, w)} material={glassMaterial(color, gold)} />
      <Edges w={w} h={h} d={w} color={color} y={h / 2} />
    </group>
  );
}

function Spire({ y, h, color }: { y: number; h: number; color: string }) {
  return (
    <group position-y={y}>
      <mesh position-y={h / 2} material={glassMaterial(color)}>
        <coneGeometry args={[0.55, h, 6]} />
      </mesh>
      <mesh position-y={h + 0.3}>
        <sphereGeometry args={[0.22, 12, 12]} />
        <meshBasicMaterial color={bright(color, 3)} toneMapped={false} />
      </mesh>
    </group>
  );
}

function Antenna({ y, h, color, x = 0, z = 0 }: { y: number; h: number; color: string; x?: number; z?: number }) {
  const tip = useRef<THREE.MeshBasicMaterial>(null);
  const phase = useMemo(() => seeded(Math.round((x + 3) * 97 + h * 13))() * 6, [x, h]);
  useFrame(({ clock }) => { // the aviation light blinks: scenery, not data
    if (tip.current) tip.current.opacity = 0.35 + 0.65 * Math.max(0, Math.sin(clock.elapsedTime * 2 + phase));
  });
  return (
    <group position={[x, y, z]}>
      <mesh position-y={h / 2}>
        <cylinderGeometry args={[0.05, 0.09, h, 6]} />
        <meshStandardMaterial color="#2a2d44" metalness={0.8} roughness={0.3} />
      </mesh>
      <mesh position-y={h}>
        <sphereGeometry args={[0.14, 10, 10]} />
        <meshBasicMaterial ref={tip} color={bright("#ff3355", 3)} toneMapped={false} transparent />
      </mesh>
    </group>
  );
}

function Helipad({ y, w, color }: { y: number; w: number; color: string }) {
  return (
    <group position-y={y}>
      <mesh position-y={0.12}>
        <cylinderGeometry args={[w * 0.42, w * 0.42, 0.24, 32]} />
        <meshStandardMaterial color="#14162a" metalness={0.5} roughness={0.4} />
      </mesh>
      <mesh rotation-x={-Math.PI / 2} position-y={0.25}>
        <ringGeometry args={[w * 0.34, w * 0.38, 40]} />
        <meshBasicMaterial color={bright(color, 2.4)} toneMapped={false} />
      </mesh>
    </group>
  );
}

/** A tower: the floors live in the square shaft (where the agent windows are); the
 *  silhouette above it is the department's own. */
export function Tower({ h, color, slot }: { h: number; color: string; slot: number }) {
  const W = BUILDING_W;
  const style = styleFor(slot);
  return (
    <group>
      <mesh position-y={0.15}>
        <boxGeometry args={[W + 1.2, 0.3, W + 1.2]} />
        <meshStandardMaterial color="#0c0d1c" metalness={0.6} roughness={0.35} />
      </mesh>
      <Edges w={W + 1.2} h={0.3} d={W + 1.2} color={color} k={1.4} y={0.15} />
      <Block w={W} h={h} y={0.3} color={color} />
      {style === "setback" && <>
        <Block w={W * 0.74} h={2.6} y={h + 0.3} color={color} />
        <Block w={W * 0.5} h={2.0} y={h + 2.9} color={color} />
        <Antenna y={h + 4.9} h={3} color={color} />
      </>}
      {style === "spire" && <>
        <Block w={W * 0.62} h={1.6} y={h + 0.3} color={color} />
        <Spire y={h + 1.9} h={7} color={color} />
      </>}
      {style === "helipad" && <>
        <Block w={W * 0.86} h={1.2} y={h + 0.3} color={color} />
        <Helipad y={h + 1.5} w={W * 0.86} color={color} />
      </>}
      {style === "twin" && <>
        <Block w={W * 0.42} h={3.4} y={h + 0.3} color={color} />
        <Antenna y={h + 3.7} h={2.2} color={color} x={-0.6} />
        <Antenna y={h + 3.7} h={3.4} color={color} x={0.6} />
      </>}
      {style === "crown" && <>
        <Block w={W * 0.8} h={1.4} y={h + 0.3} color={color} />
        {[0, 1, 2, 3].map((i) => (
          <mesh key={i} position={[Math.cos(i * Math.PI / 2) * W * 0.32, h + 2.8,
            Math.sin(i * Math.PI / 2) * W * 0.32]} rotation-y={-i * Math.PI / 2}>
            <boxGeometry args={[0.25, 2.8, W * 0.5]} />
            <meshBasicMaterial color={bright(color, 1.8)} toneMapped={false} />
          </mesh>
        ))}
      </>}
      {style === "antenna" && <>
        <Block w={W * 0.7} h={2.2} y={h + 0.3} color={color} />
        <Antenna y={h + 2.5} h={6} color={color} />
        <Antenna y={h + 2.5} h={2.4} color={color} x={1.2} z={1.2} />
      </>}
    </group>
  );
}

/** Height of the silhouette above the shaft (for signs and beams). */
export function crownH(slot: number): number {
  return { setback: 7.9, spire: 9.5, helipad: 1.8, twin: 7.2, crown: 4.4, antenna: 8.8 }[styleFor(slot)];
}

/** An empty lot: a holographic wireframe footprint with a slow scan line. */
export function HoloLot({ color }: { color: string }) {
  const scan = useRef<THREE.Mesh>(null);
  // a fixed offset per lot colour (scenery only; no randomness in the bundle)
  const phase = useMemo(() => [...color].reduce((n, ch) => n + ch.charCodeAt(0), 0) % 4, [color]);
  useFrame(({ clock }) => {
    if (scan.current) scan.current.position.y = 0.2 + ((clock.elapsedTime * 0.6 + phase) % 4);
  });
  const W = BUILDING_W;
  return (
    <group>
      <mesh rotation-x={-Math.PI / 2} position-y={0.04}>
        <planeGeometry args={[W, W]} />
        <meshBasicMaterial color={color} transparent opacity={0.04} depthWrite={false} />
      </mesh>
      <Edges w={W} h={4.2} d={W} color={color} k={0.55} y={2.1} />
      <mesh ref={scan} rotation-x={-Math.PI / 2}>
        <planeGeometry args={[W, W]} />
        <meshBasicMaterial color={bright(color, 1.2)} transparent opacity={0.07}
          toneMapped={false} side={THREE.DoubleSide} depthWrite={false} />
      </mesh>
    </group>
  );
}

/** The CEO tower: tallest, gold-trimmed glass, a crown of light. */
export function CeoTower() {
  const gold = "#f0b040";
  const crown = useRef<THREE.Mesh>(null);
  useFrame(({ clock }) => { if (crown.current) crown.current.rotation.y = clock.elapsedTime * 0.25; });
  return (
    <group>
      <mesh position-y={0.2}>
        <cylinderGeometry args={[TOWER_W * 1.2, TOWER_W * 1.3, 0.4, 8]} />
        <meshStandardMaterial color="#0e0c18" metalness={0.7} roughness={0.3} />
      </mesh>
      <Block w={TOWER_W} h={TOWER_H * 0.62} y={0.4} color={gold} gold />
      <Block w={TOWER_W * 0.8} h={TOWER_H * 0.26} y={0.4 + TOWER_H * 0.62} color={gold} gold />
      <Block w={TOWER_W * 0.58} h={TOWER_H * 0.12 - 0.4} y={0.4 + TOWER_H * 0.88} color={gold} gold />
      <mesh ref={crown} position-y={TOWER_H + 0.9}>
        <torusGeometry args={[TOWER_W * 0.42, 0.09, 8, 48]} />
        <meshBasicMaterial color={bright(gold, 3)} toneMapped={false} />
      </mesh>
      <mesh position-y={TOWER_H + 2.6}>
        <cylinderGeometry args={[0.06, 0.5, 3.4, 12, 1, true]} />
        <meshBasicMaterial color={bright(gold, 2)} toneMapped={false} transparent opacity={0.55}
          side={THREE.DoubleSide} depthWrite={false} />
      </mesh>
    </group>
  );
}

/** Jaxon: a glowing core, a rotating wireframe shell and orbiting particle rings. Its
 *  brightness and pulse come from real events per minute (store.pulses). */
export function JaxonOrb({ onClick }: { onClick: () => void }) {
  const core = useRef<THREE.MeshBasicMaterial>(null);
  const shell = useRef<THREE.Mesh>(null);
  const rings = useRef<THREE.Group>(null);
  const halo = useRef<THREE.Mesh>(null);
  const ringGeo = useMemo(() => {
    const r = seeded(11);
    return [0, 1, 2].map((k) => {
      const n = 260, pos = new Float32Array(n * 3);
      const R = 4.4 + k * 0.9;
      for (let i = 0; i < n; i++) {
        const a = (i / n) * Math.PI * 2, j = (r() - 0.5) * 0.35;
        pos.set([Math.cos(a) * (R + j), (r() - 0.5) * 0.18, Math.sin(a) * (R + j)], i * 3);
      }
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
      return g;
    });
  }, []);
  const violet = useMemo(() => bright("#9b5cff", 2.2), []);
  useFrame((_, dt) => {
    const { pulseAt, pulses } = useCity.getState();
    const now = performance.now();
    const perMin = pulses.filter((t) => now - t < 60_000).length;
    const level = Math.min(perMin / 30, 1);          // events per minute, real
    const kick = Math.max(0, 1 - (now - pulseAt) / 600); // one pulse per real event
    if (core.current) core.current.color.copy(violet).multiplyScalar(0.7 + level * 1.4 + kick * 1.2);
    if (shell.current) {
      shell.current.rotation.y += dt * (0.15 + level * 0.6);
      shell.current.rotation.x += dt * 0.05;
      shell.current.scale.setScalar(1 + kick * 0.08);
    }
    if (rings.current) rings.current.children.forEach((c, i) => {
      c.rotation.y += dt * (0.2 + i * 0.12) * (1 + level * 2) * (i % 2 ? -1 : 1);
    });
    if (halo.current) halo.current.scale.setScalar(1 + level * 0.25 + kick * 0.15);
  });
  return (
    <group name="jaxon-orb-group">
      <mesh name="jaxon-orb" onClick={(e) => { e.stopPropagation(); onClick(); }}>
        <sphereGeometry args={[1.6, 32, 32]} />
        <meshBasicMaterial ref={core} color={violet} toneMapped={false} />
      </mesh>
      <mesh ref={halo}>
        <sphereGeometry args={[2.3, 32, 32]} />
        <meshBasicMaterial color="#6a3cff" transparent opacity={0.18} depthWrite={false}
          blending={THREE.AdditiveBlending} />
      </mesh>
      <mesh ref={shell}>
        <icosahedronGeometry args={[3.2, 1]} />
        <meshBasicMaterial color={bright("#b48cff", 1.6)} wireframe toneMapped={false}
          transparent opacity={0.7} />
      </mesh>
      <group ref={rings} rotation-x={0.35}>
        {ringGeo.map((g, i) => (
          <points key={i} geometry={g} rotation-z={i * 0.5}>
            <pointsMaterial size={0.16} color={i === 1 ? bright("#f0b040", 2) : bright("#00e5ff", 1.6)}
              toneMapped={false} transparent opacity={0.85} blending={THREE.AdditiveBlending}
              depthWrite={false} sizeAttenuation />
          </points>
        ))}
      </group>
    </group>
  );
}
