// "low" on phones / low power / reduced motion: no bloom, fewer stars, 1x pixels.
export type Mode = "high" | "low" | "list";

export function hasWebGL(): boolean {
  try {
    const c = document.createElement("canvas");
    return !!(c.getContext("webgl2") || c.getContext("webgl"));
  } catch { return false; }
}

export function pickMode(): Mode {
  if (!hasWebGL()) return "list";
  const mq = (q: string) => typeof matchMedia === "function" && matchMedia(q).matches;
  const nav = navigator as Navigator & { connection?: { saveData?: boolean }; deviceMemory?: number };
  const low = mq("(pointer: coarse)") || mq("(max-width: 899px)") ||
    mq("(prefers-reduced-motion: reduce)") || !!nav.connection?.saveData ||
    (nav.deviceMemory !== undefined && nav.deviceMemory <= 4);
  return low ? "low" : "high";
}
