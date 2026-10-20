# gtp-fit

A goods-to-person technology fit tool. **Ingest** is built: it reads a client's exports in any common
format, guesses which table each file holds, lets you confirm the files and columns, and refuses data
it can't trust.

```
pip install -e .[dev,app]
python -m gtpfit.app                                   # workbench: drop files, confirm, ingest (localhost:8501)
python -m gtpfit.ingest FOLDER [--wms PROFILE.yaml]    # command line -> FOLDER/_ingest
python -m pytest -q
```

Tests use a small frozen sample client in `tests/fixtures/` (see its README). Client files stay in
`data/`, which git ignores. See `CLAUDE.md` for the plan and ground rules.
