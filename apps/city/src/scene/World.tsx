// The world around the City: a galaxy sky, stars, a glossy ground and the post-processing.
// Pure scenery: it never shows a number, so it can move when nothing is happening.
import { Environment, Lightformer, MeshReflectorMaterial } from "@react-three/drei";
import { useFrame, useThree } from "@react-three/fiber";
import {
  Bloom, ChromaticAberration, EffectComposer, SMAA, ToneMapping, Vignette,
} from "@react-three/postprocessing";
import { BlendFunction, ToneMappingMode } from "postprocessing";
import { useMemo, useRef } from "react";
import * as THREE from "three";

import { seeded } from "../lib/rand";

export const SKY_R = 520;
/** The colour far ground fades into (fog) and the sky below the horizon. */
export const HAZE = "#1c1040";

const NOISE = /* glsl */ `
float hash(vec3 p) {
  p = fract(p * 0.3183099 + 0.1);
  p *= 17.0;
  return fract(p.x * p.y * p.z * (p.x + p.y + p.z));
}
float vnoise(vec3 x) {
  vec3 i = floor(x);
  vec3 f = fract(x);
  f = f * f * (3.0 - 2.0 * f);
  return mix(mix(mix(hash(i + vec3(0, 0, 0)), hash(i + vec3(1, 0, 0)), f.x),
                 mix(hash(i + vec3(0, 1, 0)), hash(i + vec3(1, 1, 0)), f.x), f.y),
             mix(mix(hash(i + vec3(0, 0, 1)), hash(i + vec3(1, 0, 1)), f.x),
                 mix(hash(i + vec3(0, 1, 1)), hash(i + vec3(1, 1, 1)), f.x), f.y), f.z);
}
float fbm(vec3 p) {
  float v = 0.0;
  float a = 0.5;
  for (int i = 0; i < 6; i++) { v += a * vnoise(p); p *= 2.02; a *= 0.5; }
  return v;
}
`;

/** The nebula: FBM bands of purple, gold and deep blue that drift very slowly. */
function Nebula() {
  const mat = useRef<THREE.ShaderMaterial>(null);
  const uniforms = useMemo(() => ({
    uTime: { value: 0 },
    uPurple: { value: new THREE.Color("#5a2a8c") },
    uGold: { value: new THREE.Color("#f0b040") },
    uBlue: { value: new THREE.Color("#1a3a90") },
    uBase: { value: new THREE.Color("#04030c") },
    uHaze: { value: new THREE.Color(HAZE) },
  }), []);
  useFrame((_, dt) => { uniforms.uTime.value += dt; });
  return (
    <mesh name="nebula" renderOrder={-10} frustumCulled={false}>
      <sphereGeometry args={[SKY_R, 64, 32]} />
      <shaderMaterial ref={mat} side={THREE.BackSide} depthWrite={false} fog={false}
        uniforms={uniforms}
        vertexShader={/* glsl */ `
          varying vec3 vDir;
          void main() {
            vDir = normalize(position);
            gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
          }`}
        fragmentShader={/* glsl */ `
          uniform float uTime;
          uniform vec3 uPurple, uGold, uBlue, uBase, uHaze;
          varying vec3 vDir;
          ${NOISE}
          void main() {
            vec3 d = normalize(vDir);
            vec3 q = d * 2.6 + vec3(uTime * 0.006, uTime * 0.0015, -uTime * 0.004);
            float a = fbm(q);
            float b = fbm(q * 1.7 + vec3(5.2, 1.3, 2.1));
            float c = fbm(q * 0.8 + vec3(-3.1, 4.7, 1.9));
            float band = exp(-pow(d.y * 2.2 + 0.35 * sin(d.x * 3.0), 2.0) * 1.6);
            vec3 col = uBase + uPurple * pow(a, 3.0) * 1.4 * band
                     + uGold * pow(b, 4.0) * 1.3 * band + uBlue * pow(c, 3.0) * 0.9;
            col *= 1.9;
            col += uPurple * 0.35 * exp(-abs(d.y - 0.02) * 22.0);   // glow along the horizon
            col = mix(col, uHaze, smoothstep(0.12, -0.04, d.y));   // melts into the ground's haze
            gl_FragColor = vec4(col, 1.0);
            #include <colorspace_fragment>
          }`} />
    </mesh>
  );
}

/** Twinkling stars: one draw call, additive, sizes shrink with distance. */
function Stars({ count }: { count: number }) {
  const mat = useRef<THREE.ShaderMaterial>(null);
  const geo = useMemo(() => {
    const r = seeded(7);
    const pos = new Float32Array(count * 3);
    const size = new Float32Array(count);
    const phase = new Float32Array(count);
    const tint = new Float32Array(count * 3);
    const palette = [new THREE.Color("#ffffff"), new THREE.Color("#cfd8ff"),
      new THREE.Color("#ffe2a8"), new THREE.Color("#d9b8ff")];
    for (let i = 0; i < count; i++) {
      const th = r() * Math.PI * 2, ph = Math.acos(0.16 + r() * 0.84), d = SKY_R * (0.62 + r() * 0.3);
      pos[i * 3] = d * Math.sin(ph) * Math.cos(th);
      pos[i * 3 + 1] = d * Math.cos(ph);
      pos[i * 3 + 2] = d * Math.sin(ph) * Math.sin(th);
      size[i] = 0.6 + Math.pow(r(), 6) * 3.2; // mostly small, a few large
      phase[i] = r() * Math.PI * 2;
      const c = palette[Math.floor(r() * palette.length)]!;
      tint.set([c.r, c.g, c.b], i * 3);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.setAttribute("aSize", new THREE.BufferAttribute(size, 1));
    g.setAttribute("aPhase", new THREE.BufferAttribute(phase, 1));
    g.setAttribute("aTint", new THREE.BufferAttribute(tint, 3));
    return g;
  }, [count]);
  const { size: viewport } = useThree();
  const uniforms = useMemo(() => ({ uTime: { value: 0 }, uScale: { value: 1 } }), []);
  useFrame((_, dt) => {
    uniforms.uTime.value += dt;
    uniforms.uScale.value = viewport.height / 2;
  });
  return (
    <points geometry={geo} name="stars" frustumCulled={false}>
      <shaderMaterial ref={mat} transparent depthWrite={false} fog={false}
        blending={THREE.AdditiveBlending} uniforms={uniforms}
        vertexShader={/* glsl */ `
          attribute float aSize;
          attribute float aPhase;
          attribute vec3 aTint;
          uniform float uTime, uScale;
          varying float vTw;
          varying vec3 vTint;
          void main() {
            vec4 mv = modelViewMatrix * vec4(position, 1.0);
            vTw = 0.55 + 0.45 * sin(uTime * (0.6 + aSize * 0.35) + aPhase);
            vTint = aTint;
            gl_PointSize = max(aSize * uScale * 3.2 / -mv.z, 1.5);
            gl_Position = projectionMatrix * mv;
          }`}
        fragmentShader={/* glsl */ `
          varying float vTw;
          varying vec3 vTint;
          void main() {
            vec2 p = gl_PointCoord - 0.5;
            float d = length(p);
            float a = smoothstep(0.5, 0.0, d);
            gl_FragColor = vec4(vTint * a * vTw * 1.7, a * vTw);
          }`} />
    </points>
  );
}

function glowTexture(): THREE.Texture {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const g = c.getContext("2d")!;
  const rad = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  rad.addColorStop(0, "rgba(255,255,255,1)");
  rad.addColorStop(0.12, "rgba(255,240,210,0.85)");
  rad.addColorStop(0.4, "rgba(240,176,64,0.18)");
  rad.addColorStop(1, "rgba(0,0,0,0)");
  g.fillStyle = rad;
  g.fillRect(0, 0, 128, 128);
  g.globalCompositeOperation = "lighter";
  for (const [w, h] of [[128, 3], [3, 128]] as const) { // lens spikes
    const lin = g.createLinearGradient(w > h ? 0 : 64, w > h ? 64 : 0, w > h ? 128 : 64, w > h ? 64 : 128);
    lin.addColorStop(0, "rgba(255,230,190,0)");
    lin.addColorStop(0.5, "rgba(255,230,190,0.55)");
    lin.addColorStop(1, "rgba(255,230,190,0)");
    g.fillStyle = lin;
    g.fillRect(64 - w / 2, 64 - h / 2, w, h);
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

/** A handful of distant bright stars with a lens glow. */
function BrightStars() {
  const tex = useMemo(glowTexture, []);
  const spots = useMemo(() => {
    const r = seeded(31);
    return Array.from({ length: 7 }, () => {
      const th = r() * Math.PI * 2, ph = 0.95 + r() * 0.5, d = SKY_R * 0.8;
      return { pos: [d * Math.sin(ph) * Math.cos(th), d * Math.cos(ph), d * Math.sin(ph) * Math.sin(th)] as
        [number, number, number], s: 14 + r() * 16, phase: r() * 6 };
    });
  }, []);
  const refs = useRef<(THREE.Sprite | null)[]>([]);
  useFrame(({ clock }) => {
    refs.current.forEach((sp, i) => {
      if (!sp) return;
      const k = 0.85 + 0.15 * Math.sin(clock.elapsedTime * 0.7 + spots[i]!.phase);
      sp.scale.setScalar(spots[i]!.s * k);
    });
  });
  return (
    <group name="bright-stars">
      {spots.map((p, i) => (
        <sprite key={i} ref={(el) => { refs.current[i] = el; }} position={p.pos} scale={p.s}>
          <spriteMaterial map={tex} blending={THREE.AdditiveBlending} depthWrite={false}
            transparent fog={false} color="#fff2d6" />
        </sprite>
      ))}
    </group>
  );
}

/** A faint gold hex grid etched into the ground, fading out with distance. */
function Etching() {
  const uniforms = useMemo(() => ({ uGold: { value: new THREE.Color("#f0b040") },
    uCyan: { value: new THREE.Color("#3a6cff") } }), []);
  return (
    <mesh rotation-x={-Math.PI / 2} position-y={0.015} name="ground-etching" renderOrder={1}>
      <planeGeometry args={[440, 440, 1, 1]} />
      <shaderMaterial transparent depthWrite={false} uniforms={uniforms}
        vertexShader={/* glsl */ `
          varying vec2 vXZ;
          void main() {
            vXZ = (modelMatrix * vec4(position, 1.0)).xz;
            gl_Position = projectionMatrix * viewMatrix * modelMatrix * vec4(position, 1.0);
          }`}
        fragmentShader={/* glsl */ `
          uniform vec3 uGold, uCyan;
          varying vec2 vXZ;
          float hexEdge(vec2 p) {
            vec2 r = vec2(1.0, 1.7320508);
            vec2 h = r * 0.5;
            vec2 a = mod(p, r) - h;
            vec2 b = mod(p - h, r) - h;
            vec2 g = dot(a, a) < dot(b, b) ? a : b;
            g = abs(g);
            return 0.5 - max(dot(g, normalize(r)), g.x);
          }
          void main() {
            float dist = length(vXZ);
            vec2 p = vXZ / 4.0;
            float e = hexEdge(p);
            float w = fwidth(e);
            float line = 1.0 - smoothstep(0.0, w * 1.5, e - 0.006);
            line *= smoothstep(0.12, 0.02, w); // far away the lines would alias: let them go
            float fade = exp(-pow(dist / 85.0, 2.0));
            float inner = smoothstep(12.0, 24.0, dist); // keep the plaza under the tower clean
            vec3 col = mix(uGold, uCyan, smoothstep(30.0, 110.0, dist)) * line;
            float a = line * 0.17 * fade * mix(0.3, 1.0, inner);
            gl_FragColor = vec4(col, a);
            #include <colorspace_fragment>
          }`} />
    </mesh>
  );
}

/** Glossy dark ground: a blurred mirror on desktop, plain dark gloss on phones. */
function Ground({ reflect }: { reflect: boolean }) {
  return (
    <group name="ground">
      <mesh rotation-x={-Math.PI / 2} position-y={-0.01} receiveShadow>
        <circleGeometry args={[1300, 96]} />
        {reflect ? (
          <MeshReflectorMaterial
            blur={[240, 80]} resolution={256} mixBlur={1} mixStrength={0.4} mirror={0.4}
            roughness={0.75} depthScale={0.6} minDepthThreshold={0.4} maxDepthThreshold={1.3}
            color="#07050f" metalness={0.6} />
        ) : (
          <meshStandardMaterial color="#07050f" roughness={0.35} metalness={0.7} />
        )}
      </mesh>
      <Etching />
    </group>
  );
}

/** Slow ambient life of the sky only: nothing here reacts to (or fakes) activity. */
export function World({ light }: { light: boolean }) {
  return (
    <group name="world">
      <Nebula />
      <Stars count={light ? 1500 : 5000} />
      <BrightStars />
      <Ground reflect={!light} />
      {/* what the glass towers reflect: the nebula's colours as soft light panels,
          rendered once into a small cube map (no network, no HDR file) */}
      <Environment resolution={light ? 64 : 128} frames={1}>
        <color attach="background" args={["#05040c"]} />
        <Lightformer form="ring" intensity={2.2} color="#7a3cc8" scale={60} position={[0, 40, -80]} />
        <Lightformer form="rect" intensity={1.6} color="#f0b040" scale={[120, 14, 1]} position={[0, 18, 90]}
          rotation-y={Math.PI} />
        <Lightformer form="rect" intensity={1.2} color="#2a5cff" scale={[90, 30, 1]} position={[-90, 25, 0]}
          rotation-y={Math.PI / 2} />
        <Lightformer form="rect" intensity={0.8} color="#ff3df2" scale={[60, 20, 1]} position={[90, 20, 0]}
          rotation-y={-Math.PI / 2} />
      </Environment>
      <hemisphereLight args={["#5a6cff", "#140a26", 0.35]} />
      <directionalLight position={[60, 90, 40]} intensity={0.55} color="#dfe4ff" />
    </group>
  );
}

/** Bloom only on bright emissive things (threshold 0.85), ACES, a soft vignette, SMAA. */
export function Post() {
  return (
    <EffectComposer multisampling={0}>
      <Bloom intensity={0.6} luminanceThreshold={0.85} luminanceSmoothing={0.12} mipmapBlur />
      <ChromaticAberration blendFunction={BlendFunction.NORMAL}
        offset={new THREE.Vector2(0.0004, 0.0004)} radialModulation modulationOffset={0.4} />
      <ToneMapping mode={ToneMappingMode.ACES_FILMIC} />
      <Vignette offset={0.32} darkness={0.62} />
      <SMAA />
    </EffectComposer>
  );
}
