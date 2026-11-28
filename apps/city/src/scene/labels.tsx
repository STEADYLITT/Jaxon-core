import { Html } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { createContext, useContext, useRef, type RefObject } from "react";

// Labels portal into a stable overlay outside the canvas (drei's default target changes
// when the canvas connects its events, which can drop an early label's content).
export const LabelLayer = createContext<RefObject<HTMLDivElement | null> | null>(null);

export function Label({ position, className, children }: {
  position: [number, number, number]; className: string; children: React.ReactNode;
}) {
  const layer = useContext(LabelLayer);
  return (
    <Html position={position} center className={className} pointerEvents="none"
      portal={layer as RefObject<HTMLElement> | undefined}>{children}</Html>
  );
}


/** Keeps every sign readable: about 6 times a second, labels that would overlap on screen are
 *  nudged upward (CSS `translate`, so drei's own positioning is untouched). The CEO sign is
 *  placed first, then department signs, then empty-lot names; nearer (lower) labels keep
 *  their spot and the ones behind them move up. Ticker text on the ring road is left alone. */
export function LabelDeclutter() {
  const layer = useContext(LabelLayer);
  const tick = useRef(0);
  useFrame(() => {
    const now = performance.now();
    if (now - tick.current < 150 || !layer?.current) return;
    tick.current = now;
    const rank = (el: HTMLElement) =>
      el.classList.contains("ceo") ? 0 : el.classList.contains("holo") ? 1 : 2;
    const items = [...layer.current.querySelectorAll<HTMLElement>(".label:not(.ticker-holo)")]
      .map((el) => {
        const r = el.getBoundingClientRect();
        const off = Number(el.dataset.off || 0);
        return { el, x: r.left, w: r.width, h: r.height, y: r.top - off, off };
      })
      .filter((i) => i.w > 0 && i.h > 0)
      .sort((a, b) => rank(a.el) - rank(b.el) || b.y - a.y);
    const placed: { x: number; y: number; w: number; h: number }[] = [];
    const PAD = 3;
    for (const i of items) {
      let top = i.y;
      for (let pass = 0; pass < 8; pass++) {
        const hit = placed.find((p) => i.x < p.x + p.w + PAD && p.x < i.x + i.w + PAD
          && top < p.y + p.h + PAD && p.y < top + i.h + PAD);
        if (!hit) break;
        top = hit.y - i.h - PAD; // just above the label it would cover
      }
      placed.push({ x: i.x, y: top, w: i.w, h: i.h });
      const off = Math.round(top - i.y);
      if (off !== i.off) {
        i.el.dataset.off = String(off);
        i.el.style.translate = off ? `0 ${off}px` : "";
      }
    }
  });
  return null;
}
