# ADR-055: The City's cinematic look

**Status:** accepted, 2026-11-28 (built in five small PRs)

## Rule
The **scenery** may move and glow when nothing is happening: sky, stars, light, materials, aviation lights. Anything that **carries meaning** still comes only from real rows and events (Rule Zero):
- building heights come from floors;
- windows come from agents and their statuses;
- flights and beams come from events;
- the orb's brightness comes from events per minute.

## Parts
1. **World** (`scene/World.tsx`):
   - FBM nebula on an inverted sphere. Six octaves of value noise along the view direction. Purple `#5a2a8c`, gold `#f0b040` and blue `#1a3a90`, in the ad's band formula, drifting slowly.
   - Twinkling stars: 5k, one `Points` draw, additive.
   - Seven distant bright stars with a lens glow.
   - Glossy reflective ground (`MeshReflectorMaterial`, mirror 0.4) with a gold hex etching that fades with distance.
   - Dusk haze at the horizon.
2. **Post:** bloom only above luminance 0.85 (strength 0.6, mipmap blur), so only emissive things glow. Plus ACES tone mapping, a light chromatic aberration (desktop only), a vignette and SMAA with 4× MSAA. Labels are HTML, so they never bloom.
3. **Low mode** (phones, low memory, reduced motion, slow GPUs): no reflector, 1.5k stars, no post-processing.
4. **Architecture** (`scene/Architecture.tsx`):
   - **Towers.** Every department is a dark-glass tower: `MeshPhysicalMaterial`, clearcoat 1, roughness 0.1, metalness 0.6, with edges that glow in the department's colour. Materials and geometries are shared, so draw cost stays low.
   - **Silhouettes.** The square shaft holds the floors, which are real data. Above it sits the department's own crown, chosen from its slot so it never changes: setbacks, a spire, a helipad, twin antennas (aviation lights blink), a crown of light, or an antenna mast.
   - **Signs.** A holographic sign carries the department name. It is a crisp HTML label and never blooms.
   - **Windows.** There is one window per agent, coloured by its real status: cyan working, green done, amber waiting, red failed, gray offline, soft blue idle-but-online. Active states glow. Only online agents flicker.
   - **Empty lots** show a wireframe footprint with a slow scan line.
   - **Failure siren.** A department whose failure siren is sounding pulses a red glass shell.
   - **CEO tower.** The tallest tower, in gold-trimmed glass, with a rotating crown of light.
   - **Jaxon orb.** A core, a rotating wireframe shell and three orbiting particle rings. Brightness, spin and pulse come from the real events per minute.
   - **Reflections.** Glass reflects a small cube map made from coloured light panels. Nothing is fetched over the network.
5. **Life** (`scene/Life.tsx`). Every effect is caused by a real event:
   - **Envelopes.** Each envelope rides a low-poly courier drone: emissive rotors, the card hanging below, and a light trail on desktop. Approvals carry a red wax-seal glow and land on the CEO tower. Flight time is 2.6 s on an eased arc.
   - **Task done or failed.** A green or red light column (core plus halo) rises from the department's roof, with a shockwave ring at its base. It lasts 2.2 s.
   - **Revenue.** A gold beam and a burst of 36 instanced gold coins along a fixed spiral (no randomness).
   - **Ring road.** A glowing two-lane gold and cyan track. Above it, the three newest real ticker events float as holographic text (desktop).
   - **Debugging hooks.** `__city.flown` counts the envelopes sent, so tests don't race a short flight. `?fxslow` stretches effect durations 8× for screenshots on a slow software renderer.
6. **Camera** (`CameraRig` in `scene/City.tsx`):
   - **Cinematic intro.** On first load the camera flies in from deep space through the nebula band on a curved path and settles on the overview in 3.6 s (eased). Any key, click, touch or wheel skips it (finishes in 0.35 s). Labels fade in on arrival. It is off for reduced motion and under automation (tests opt in with `?intro`). `html[data-city-intro]` reads `playing` / `done` / `off`.
   - **Fly-to.** Clicking a building, the orb, the CEO tower or a floor flies there in a fixed 1.5 s eased curve instead of a drifting lerp.
   - **Idle drift.** After 12 s without input on the overview, the orbit turns very slowly (`autoRotateSpeed` 0.25, about 4 minutes a lap). Any input stops it. This is scenery motion, not data.
7. **Interiors** (`scene/Office.tsx`): glass desks with edges lit in the agent's status colour, holographic monitors (lit frame, additive pane), robots in clearcoat with glowing status-coloured eyes that blink only while the agent is really working, a mirrored floor on desktop (plain on phones), a gold grid, smoked-glass walls and warm/department-coloured fill lights.
8. Later: the HUD (glass panels, type, command pill) and adaptive resolution.

## Proof
`tests/city_shots.py` takes desktop (1440×900) and iPhone 15 screenshots, idle and right after a real command. They are saved under `docs/city-shots/<part>/`.
