# app/static

Served by the workbench at `/app/static/...` (`--server.enableStaticServing true` in `app/__main__.py`), so
everything here works offline.

| Path | What | Source |
|---|---|---|
| `hai-loader/hai-loader.js` | HAI Robotics 3D loading animation (`<hai-loader>`), shown in the middle of the screen while a page is working (`app/loader.py`). | `vendor/hai-loader/HAI Robot Loading Animation.zip`, converted from Windows-1252 to UTF-8. One change: `import * as THREE from 'three'` became `from '../three/three.module.min.js'`. |
| `brand/hai-robotics-primary.svg`, `brand/hai-robotics-primary-reverse.svg` | The Hai Robotics primary logo (full colour, and white for dark mode), top left of every page (`app/theme.py`). | The Hai Robotics design system's Logos group (https://claude.ai/artifact/KBgzRxGv1ydpfyh9YGQevd), unchanged; a test pins their checksums. Never redraw or recolour them. |
| `three/three.module.min.js`, `three/three.core.min.js` | three.js r184, the loader's only dependency. | `https://unpkg.com/three@0.184.0/build/`, the version the loader was tested with. MIT licence in `three/LICENSE`. |

Check by hand after changing any of these: run a page for a few seconds; the robot should appear after
about 0.6 s in the middle of the screen, with Stop still in the top bar, and disappear when the run ends.
