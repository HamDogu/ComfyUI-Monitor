# Changelog

Versions follow `MAJOR.MINOR.PATCH`: patch for fixes, minor for new features, major for big or
incompatible changes. The running version is shown next to the title in the app (and at
`/api/version`). Add notes under **Unreleased** as you work, then run `py -3 release.py minor`
(or `patch` / `major`) to stamp them, commit and tag.

## [Unreleased]

## [6.2.1] - 2026-10-27

### Changed
- README rewritten for GitHub: features, requirements, installation, configuration, updating.
  Detailed tab guide, troubleshooting and older history moved to `docs/USER_GUIDE.md`.
- `config.json` is no longer tracked; copy `config.example.json` to create it.

### Fixed
- A request the phone cancels (page reload, folder change, app in the background) no longer
  prints a `ConnectionAbortedError` traceback in the monitor window.

### Added
- Requests that take over 3 seconds are logged in the monitor window ("Slow request: ..."), to
  show what is slow when the app hangs. Downloads, streams, uploads and chat are not logged.

## [6.2.0] - 2026-10-27

### Added
- **⟳ Restart** button next to the version: restarts the monitor on the PC to load updated code,
  then reloads the page once the new copy answers. ComfyUI and its queue are not affected.
- `start_monitor.bat` relaunches the app when it exits for a restart (exit code 3); run directly
  with Python, the app starts a fresh copy of itself instead.

## [6.1.0] - 2026-10-27

### Added
- Version number shown next to the app title and in the footer, served at `/api/version`.
  A `+dirty` or `+N-gHASH` suffix means the running code has changes since the last release.
- `VERSION`, `CHANGELOG.md` and `release.py` for numbered releases with git tags.
- Small JPEG thumbnails (max 512 px) for images and videos, cached on disk in `.thumbs/`.
- A background thread pre-renders thumbnails for new files in the output and input folders,
  at below-normal CPU priority so it never competes with ComfyUI.

### Changed
- Output browser, Media Assets and recent-job tiles load thumbnails instead of full-size
  originals (the first output page went from ~240 MB to ~2 MB). Tapping still opens the original.
- Only 4 grid videos autoplay at once, and videos are unloaded when scrolled off screen, so
  the phone's browser no longer runs out of memory and reloads the tab.
- Media Assets files and saved job outputs are read straight from disk instead of through
  ComfyUI, so a busy ComfyUI no longer stalls them.
- The Media Assets list waits at most 5 s for ComfyUI, then falls back to the last good list
  or a listing read from disk.
- Thumbnails and files with versioned URLs may be cached by the phone's browser.

## [6.0.0] - 2026-10-27

- Baseline: the app as it was when version tracking started.
