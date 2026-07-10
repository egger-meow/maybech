# Release Process

This document defines the GitHub release path for Maybech.

## Versioning

Maybech uses SemVer-style project versions before 1.0:

- `MAJOR.MINOR.PATCH` is stored in `pyproject.toml`, `src/version.py`,
  `frontend/package.json`, and `frontend/package-lock.json`.
- Git tags use a leading `v`, for example `v0.1.0`.
- While the project is under `1.0.0`, minor releases may still include operator
  workflow or API changes that require careful reading of the release notes.
- SQLite schema versions are component migration versions and are not the same
  as the project release version.

The first GitHub release should be `v0.1.0`. Treat shorthand references to
"v0" as the `v0.1.0` baseline unless a later `v0.x.y` tag is named explicitly.

## Release Checklist

1. Ensure the working tree contains only intended release changes.
2. Update version metadata in:
   - `pyproject.toml`
   - `src/version.py`
   - `frontend/package.json`
   - `frontend/package-lock.json`
3. Update `CHANGELOG.md` with the release date, included capabilities, safety
   notes, known limits, and migration notes when applicable.
4. Regenerate API/frontend contracts if API schemas changed:

```powershell
uv run python scripts/generate_openapi_types.py
```

5. Run backend tests:

```powershell
uv run pytest
```

If local uv cache permissions fail on Windows, use the active virtual
environment:

```powershell
.venv\Scripts\python.exe -m pytest
```

6. Run frontend verification:

```powershell
cd frontend
npm run verify
```

7. Inspect runtime safety docs for drift:
   - `README.md`
   - `docs/runtime-status.md`
   - `docs/build-status.md`
   - `docs/deployment.md`
   - `toImprove.md`
8. Commit the release prep, then create and push an annotated tag:

```powershell
git tag -a v0.1.0 -m "Release v0.1.0"
git push origin main
git push origin v0.1.0
```

9. Create the GitHub release from the tag. Use `CHANGELOG.md` as the source for
   release notes and include the exact validation commands that passed.

## Release Notes Template

```markdown
## Summary

Maybech v0.1.0 is the first GitHub release baseline for the local-first OKX
perpetual trading workspace.

## Included

- ...

## Safety Notes

- ...

## Validation

- `uv run pytest`
- `cd frontend && npm run verify`

## Known Limits

- ...
```
