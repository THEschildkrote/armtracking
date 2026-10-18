# Iris frontend

The existing `sponsors/showcase/index.html` now presents a black, translucent visual
system with a procedural canvas iris. No image CDN, font download or new JavaScript
dependency is needed. The supplied iris photograph informs the aesthetic; it is not
redistributed. Pointer proximity changes the glow and the pupil follows the cursor.
Interior views move the iris offscreen and vary its hue. Reduced-motion preferences
disable pupil tracking and breathing; keyboard focus remains visible.

The public reference at https://look-website-kappa.vercel.app was inspected. Its
landing / object examples / technology / guided demo structure is recreated locally;
this is not an import of its unpublished Figma source or 3D model assets. The local
object illustrations are SVG concepts. Medicine and phone remain concept examples;
only mug and water bottle are currently selectable in the connected demo.

Navigation:
- `#home`: iris landing, object examples and technology sections.
- `#story`, `#technology`: direct links to landing sections.
- `#studio`: camera placeholder, Chalk ranking, voice/blink confirmation, virtual arm,
  live requests and Select / Confirm / Complete progress.
- `#routines`: Factory batch analysis, validated-prior deployment and CASAS context.
- `#evaluation`: the existing custom evaluation action and text results.

Every existing `/api/` action uses the same backend implementation. No core gaze,
eye-tracking, arm, or intent files were changed. No API requests run from the iris.
Camera and arm remain simulated in this showcase. The public Vercel website has
not been replaced or deployed by this change.

Start the existing showcase server with the private sponsor config as documented
in SPONSOR_SHOWCASE.md, then visit http://127.0.0.1:8765/.

Verification: inline scripts pass Node syntax checks; IDs are unique and all literal
JavaScript element targets exist. The restyled controls returned a live Chalk query
(~200 ms), gated selection until confirmation, completed a simulated blink grab,
and produced the real held-out CASAS evaluation. Factory's saved session/report
renders on the routines page; its backend runner is unchanged.
