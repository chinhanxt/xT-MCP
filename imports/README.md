# Imported projects

- `dify-rea/`: xT Studio reconstruction project copied from `projects/reclaw/repos/dify-rea`.
- `waretrack-3d/`: WareTrack 3D, selected as the replacement warehouse visualization. The unified build serves it at `/waretrack/`.

The Studio copy excludes dependencies, build output, runtime data, logs and Git metadata. WareTrack 3D retains the supplied project's metadata. Install each project's dependencies from its own `package-lock.json`.

WareTrack 3D currently uses simulated data and has no automated test script; the unified checks explicitly report this and verify its production build. The previous `waretrack/` copy has been removed at the project owner's request.
