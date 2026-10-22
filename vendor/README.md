# vendor

Third-party and HAI-supplied files, kept as received. The code that uses them lives under `src/`.

| File | What it is | Used from |
|---|---|---|
| `hai-loader/HAI Robot Loading Animation.zip` | The HAI Robotics 3D loading animation (`<hai-loader>` web component, its README and a demo page), as received on 2026-10-22. | `src/gtpfit/app/static/hai-loader/hai-loader.js`: a UTF-8 copy with one change (three.js is imported from the local copy in `static/three/` instead of the bare `'three'`), shown by `src/gtpfit/app/loader.py` while a workbench page is working. |

To update the loader: replace the zip, then re-copy `hai-loader.js` into `src/gtpfit/app/static/hai-loader/`
with the same one-line import change (see `src/gtpfit/app/static/README.md`) and run `python -m pytest -q`.
