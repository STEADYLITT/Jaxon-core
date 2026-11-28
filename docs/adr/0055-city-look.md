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
4. Later parts: architecture and the orb, life effects, the camera intro and interiors, and the HUD.

## Proof
`tests/city_shots.py` takes desktop (1440×900) and iPhone 15 screenshots, idle and right after a real command. They are saved under `docs/city-shots/<part>/`.
