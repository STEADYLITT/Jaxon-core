// Steady types a goal. It becomes a real command (a workflow run); the letter then flies
// from the CEO tower to Jaxon because the command.issued event says so, and Jaxon's
// routing shows up as command.routed with its reason.
import { useState } from "react";

import { post } from "../lib/api";

export function CommandBar() {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  async function send(e: React.FormEvent) {
    e.preventDefault();
    const objective = text.trim();
    if (!objective || busy) return;
    setBusy(true); setMsg("");
    try {
      await post("/commands", { type: "PLAN", objective, route: true,
        idempotency_key: `city-${Date.now()}-${objective.length}` });
      setText("");
      setMsg("Sent to Jaxon.");
    } catch (err) { setMsg(`Not sent: ${(err as Error).message}`); }
    setBusy(false);
  }
  return (
    <form className="command-bar" onSubmit={send} aria-label="Command Jaxon">
      <input id="command" value={text} onChange={(e) => setText(e.target.value)} maxLength={5000}
        placeholder="Tell Jaxon what to do… e.g. Write an Instagram post about the grip"
        aria-label="Command" autoComplete="off" enterKeyHint="send" />
      <button type="submit" disabled={busy || !text.trim()}>{busy ? "Sending…" : "Send"}</button>
      {msg && <span className="cmd-msg" role="status">{msg}</span>}
    </form>
  );
}
