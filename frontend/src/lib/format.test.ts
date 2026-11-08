import assert from "node:assert/strict";
import { test } from "node:test";
import { durationMs, formatDuration, isTerminal, statusClasses, truncate } from "./format.ts";

test("formatDuration", () => {
  assert.equal(formatDuration(null), "—");
  assert.equal(formatDuration(250), "250 ms");
  assert.equal(formatDuration(1500), "1.5 s");
  assert.equal(formatDuration(125_000), "2m 5s");
});

test("durationMs", () => {
  assert.equal(durationMs("2026-01-01T00:00:00Z", "2026-01-01T00:00:02Z"), 2000);
  assert.equal(durationMs(null, "2026-01-01T00:00:00Z"), null);
  assert.equal(durationMs("2026-01-01T00:00:02Z", "2026-01-01T00:00:00Z"), null);
});

test("isTerminal / statusClasses / truncate", () => {
  assert.ok(isTerminal("failed") && !isTerminal("running"));
  assert.match(statusClasses("succeeded"), /emerald/);
  assert.equal(truncate("abcdef", 4), "abc…");
  assert.equal(truncate("abc", 4), "abc");
});
