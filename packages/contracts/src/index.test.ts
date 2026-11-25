import { describe, expect, it } from "vitest";
import {
  AUTONOMY_LEVELS,
  COMMAND_TYPES,
  ENTITY_NAMES,
  TASK_STATES,
  isTerminal,
} from "./index";

describe("contracts", () => {
  it("has the six spec task states", () => {
    expect(TASK_STATES).toEqual([
      "queued",
      "running",
      "waiting_approval",
      "succeeded",
      "failed",
      "canceled",
    ]);
  });

  it("marks only succeeded/failed/canceled as terminal", () => {
    expect(TASK_STATES.filter(isTerminal)).toEqual(["succeeded", "failed", "canceled"]);
  });

  it("has the nine spec commands", () => {
    expect(COMMAND_TYPES).toHaveLength(9);
    expect(COMMAND_TYPES).toContain("STOP");
  });

  it("has autonomy levels 0-3", () => {
    expect(Object.values(AUTONOMY_LEVELS)).toEqual([0, 1, 2, 3]);
  });

  it("has the sixteen spec entities with no duplicates", () => {
    expect(new Set(ENTITY_NAMES).size).toBe(16);
  });
});
