import { Html } from "@react-three/drei";
import { createContext, useContext, type RefObject } from "react";

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

