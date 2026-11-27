// Jackson's 3D core: a pulsing lattice for Jackson, his agents as nodes orbiting it, linked
// by animated light. Loaded only on wide screens with WebGL and no reduced-motion / low-power
// hint; everything it shows also exists as accessible HTML (agent list, status line).
// Draws only real records handed over by app.js (window.__jackson.state); nothing invented.
import * as THREE from "/console/vendor/three.module.min.js";

const CYAN = 0x5ee7ff, GOLD = 0xffc85c, RED = 0xff5c6c, DIM = 0x6f86a3;
const STATE_COLOR = { idle: DIM, running: CYAN, waiting_approval: GOLD, error: RED };

export function start(canvas, labels, getState, onAgent) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true,
    powerPreference: "low-power" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
  camera.position.set(0, 3.2, 8.2);
  camera.lookAt(0, 0, 0);

  // Core: wire lattice + inner glow + a halo of points.
  const core = new THREE.Group();
  const lattice = new THREE.Mesh(new THREE.IcosahedronGeometry(1.15, 2),
    new THREE.MeshBasicMaterial({ color: CYAN, wireframe: true, transparent: true,
      opacity: 0.55 }));
  const glow = new THREE.Mesh(new THREE.SphereGeometry(0.72, 32, 32),
    new THREE.MeshBasicMaterial({ color: CYAN, transparent: true, opacity: 0.35,
      blending: THREE.AdditiveBlending }));
  const haloPts = new Float32Array(900 * 3);
  for (let i = 0; i < 900; i += 1) {
    const u = Math.random() * 2 - 1, th = Math.random() * Math.PI * 2;
    const r = 1.6 + Math.random() * 0.25, s = Math.sqrt(1 - u * u);
    haloPts.set([r * s * Math.cos(th), r * u, r * s * Math.sin(th)], i * 3);
  }
  const haloGeo = new THREE.BufferGeometry();
  haloGeo.setAttribute("position", new THREE.BufferAttribute(haloPts, 3));
  const halo = new THREE.Points(haloGeo, new THREE.PointsMaterial({ color: GOLD, size: 0.025,
    transparent: true, opacity: 0.7 }));
  core.add(lattice, glow, halo);
  scene.add(core);

  // Floor grid.
  const grid = new THREE.GridHelper(40, 40, CYAN, CYAN);
  grid.position.y = -2.4;
  grid.material.transparent = true;
  grid.material.opacity = 0.12;
  scene.add(grid);

  // Agents: nodes + links, rebuilt when the agent list changes.
  let nodes = [];
  let signature = "";
  const nodeGeo = new THREE.SphereGeometry(0.16, 16, 16);

  function rebuild(agents) {
    nodes.forEach((n) => { scene.remove(n.mesh); scene.remove(n.line); n.label.remove(); });
    nodes = agents.map((a, i) => {
      const color = STATE_COLOR[a.state] || DIM;
      const mesh = new THREE.Mesh(nodeGeo, new THREE.MeshBasicMaterial({ color }));
      mesh.userData.id = a.id;
      const lineGeo = new THREE.BufferGeometry().setFromPoints(
        [new THREE.Vector3(), new THREE.Vector3()]);
      const line = new THREE.Line(lineGeo, new THREE.LineBasicMaterial({ color,
        transparent: true, opacity: 0.5 }));
      const label = document.createElement("button");
      label.type = "button";
      label.className = "node-label";
      label.tabIndex = -1; // the HTML agent list is the keyboard path
      label.textContent = a.name;
      label.addEventListener("click", () => onAgent(a.id));
      labels.appendChild(label);
      scene.add(mesh, line);
      return { id: a.id, state: a.state, mesh, line, label,
        radius: 3 + (i % 3) * 0.55, speed: 0.12 + (i % 5) * 0.03,
        phase: (i / Math.max(agents.length, 1)) * Math.PI * 2, tilt: ((i % 4) - 1.5) * 0.35 };
    });
  }

  const raycaster = new THREE.Raycaster();
  canvas.addEventListener("click", (ev) => {
    const r = canvas.getBoundingClientRect();
    const p = new THREE.Vector2(((ev.clientX - r.left) / r.width) * 2 - 1,
      -((ev.clientY - r.top) / r.height) * 2 + 1);
    raycaster.setFromCamera(p, camera);
    const hit = raycaster.intersectObjects(nodes.map((n) => n.mesh))[0];
    if (hit) onAgent(hit.object.userData.id);
  });

  function resize() {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  new ResizeObserver(resize).observe(canvas);
  resize();

  const v = new THREE.Vector3();
  const clock = new THREE.Clock();
  function frame() {
    requestAnimationFrame(frame);
    if (document.hidden) return; // no work in a background tab
    const st = getState();
    const sig = st.agents.map((a) => `${a.id}:${a.state}`).join("|");
    if (sig !== signature) { signature = sig; rebuild(st.agents); }
    const t = clock.getElapsedTime();
    const speed = st.stopped ? 0.6 : st.thinking ? 4.2 : 1.3;
    const pulse = 1 + (st.stopped ? 0.02 : 0.07) * Math.sin(t * speed);
    const color = st.stopped ? RED : CYAN;
    lattice.material.color.setHex(color);
    glow.material.color.setHex(color);
    glow.scale.setScalar(pulse * (st.thinking ? 1.12 : 1));
    glow.material.opacity = st.thinking ? 0.5 + 0.2 * Math.sin(t * speed) : 0.32;
    lattice.rotation.y = t * 0.25;
    lattice.rotation.x = t * 0.08;
    halo.rotation.y = -t * 0.05;
    grid.position.z = (t * 0.6) % 1;
    const w = canvas.clientWidth, h = canvas.clientHeight;
    nodes.forEach((n) => {
      const a = n.phase + t * n.speed;
      n.mesh.position.set(Math.cos(a) * n.radius, Math.sin(a * 1.3) * 0.5 + n.tilt,
        Math.sin(a) * n.radius);
      const pos = n.line.geometry.attributes.position;
      pos.setXYZ(1, n.mesh.position.x, n.mesh.position.y, n.mesh.position.z);
      pos.needsUpdate = true;
      n.line.material.opacity = n.state === "running" ? 0.35 + 0.35 * Math.sin(t * 6 + n.phase)
        : 0.25;
      v.copy(n.mesh.position).project(camera);
      n.label.style.left = `${((v.x + 1) / 2) * w}px`;
      n.label.style.top = `${((1 - v.y) / 2) * h}px`;
      n.label.style.display = v.z < 1 ? "" : "none";
    });
    renderer.render(scene, camera);
  }
  frame();
  return renderer;
}
