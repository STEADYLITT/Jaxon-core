import { lazy, Suspense, useEffect, useRef, useState } from "react";

import { follow, getState, manualSignIn, startAuth, type SignIn } from "./lib/api";
import { windowState } from "./lib/layout";
import { pickMode, type Mode } from "./lib/mode";
import { useCity } from "./lib/store";
import { Hud } from "./ui/Hud";
import { ListView } from "./ui/ListView";
import { CommandBar } from "./ui/CommandBar";
import { Panels } from "./ui/Panels";
import { Ticker } from "./ui/Ticker";

const City = lazy(() => import("./scene/City"));

declare global { interface Window { __city?: Record<string, unknown> } }

function useLive(ready: boolean) {
  const refetch = useRef<number | undefined>(undefined);
  useEffect(() => {
    if (!ready) return;
    let stopped = false;
    const load = () => getState().then((s) => useCity.getState().setSnapshot(s)).catch(() => undefined);
    load().then(() => follow(() => useCity.getState().lastEventId, (e) => {
      useCity.getState().onEvent(e);
      // statuses and numbers always come back from the database, never guessed
      window.clearTimeout(refetch.current);
      refetch.current = window.setTimeout(load, 700);
    }, () => stopped));
    const every = window.setInterval(load, 15_000); // offline/heartbeat changes
    return () => { stopped = true; window.clearInterval(every); };
  }, [ready]);
}

function useDebugHook(mode: Mode) {
  // Read by the acceptance tests: everything here is derived from the store.
  useEffect(() => useCity.subscribe((st) => {
    const s = st.snapshot;
    const states: Record<string, number> = {};
    s?.agents.filter((a) => a.department_id).forEach((a) => {
      const k = windowState(a); states[k] = (states[k] ?? 0) + 1;
    });
    window.__city = {
      mode, lastEventId: st.lastEventId, windows: states,
      envelopes: st.active.filter((e) => e.kind === "envelope").length,
      queued: st.queue.length, beams: st.active.filter((e) => e.kind === "beam").length,
      staffed: s?.departments.filter((d) => d.staffed).map((d) => d.key) ?? [],
      inbox: s?.inbox.approvals.length ?? 0, demo: s?.demo ?? false,
    };
  }), [mode]);
}

export function App() {
  const [auth, setAuth] = useState<SignIn | null>(null);
  const [mode] = useState<Mode>(() => pickMode());
  const listView = useCity((st) => st.listView);
  const toggle = useCity((st) => st.toggleList);
  const demo = useCity((st) => st.snapshot?.demo ?? false);
  const has = useCity((st) => !!st.snapshot);
  const signInEl = useRef<HTMLDivElement>(null);
  useEffect(() => {
    startAuth(() => signInEl.current).then(setAuth)
      .catch((e: Error) => setAuth({ kind: "error", message: `Sign-in couldn't start (${e.message}).` }));
  }, []);
  useLive(auth?.kind === "ready");
  useDebugHook(mode);
  const showList = mode === "list" || listView;

  if (auth?.kind !== "ready") {
    return (
      <div className="signin">
        <h1>JAXON OS</h1>
        <div ref={signInEl} />
        {auth?.kind === "error" && <p className="err" role="alert">{auth.message}</p>}
        {auth?.kind === "manual" && (
          <form onSubmit={(e) => {
            e.preventDefault();
            const f = new FormData(e.currentTarget);
            manualSignIn(String(f.get("token")), String(f.get("tenant")));
            setAuth({ kind: "ready" });
          }}>
            <label>Token <input name="token" id="token" autoComplete="off" /></label>
            <label>Tenant <input name="tenant" id="tenant" autoComplete="off" /></label>
            <button id="go" type="submit">Enter the city</button>
          </form>
        )}
      </div>
    );
  }
  return (
    <div className={`app mode-${mode}`} data-mode={mode}>
      {demo && <div className="demo-banner" role="alert">DEMO DATA — not real activity</div>}
      <Hud />
      {mode !== "list" && (
        <button className="toggle" id="list-toggle" onClick={toggle} aria-pressed={listView}>
          {listView ? "City" : "List"}
        </button>
      )}
      {!has && <p className="loading">Loading the city…</p>}
      {has && showList && <ListView />}
      {has && mode !== "list" && (
        // stays mounted while the list is shown: no WebGL rebuild, no lost state
        <div className="stage" hidden={showList}>
          <Suspense fallback={<p className="loading">Building the city…</p>}><City mode={mode} /></Suspense>
        </div>
      )}
      {!showList && <a className="nav-console" href="/console">Classic console</a>}
      <Panels />
      <CommandBar />
      <Ticker />
    </div>
  );
}
