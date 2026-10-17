# Imported projects

- `dify-rea/`: xT Studio reconstruction project copied from `projects/reclaw/repos/dify-rea`.
- `waretrack-3d/`: WareTrack 3D, selected as the replacement warehouse visualization. The unified build serves it at `/waretrack/`.

The Studio copy excludes dependencies, build output, runtime data, logs and Git metadata. WareTrack 3D retains the supplied project's metadata. Install each project's dependencies from its own `package-lock.json`.

WareTrack 3D currently uses simulated data. Its `npm test` (Node built-in test runner, no extra dependency) covers the data layer (`src/data`, `src/state/store.js`); the 3D scene and simulation loop are verified only by the production build. The previous `waretrack/` copy has been removed at the project owner's request.

## waretrack-3d: provenance and license

- Author: NguyenThong251
- Upstream: <https://github.com/NguyenThong251/waretrack-3d> (upstream `.git` history removed from this copy)
- Imported commit: `14b8edc`
- License: chia sẻ cho dự án bởi thành viên team (NguyenThong251), phát hành theo Apache-2.0 của xT (decision D-UNI-WT-1, `docs/decisions.md`). The upstream repository itself has no LICENSE file; the Apache-2.0 grant applies to this copy as shared by the author for the xT project.
- Local changes: the upstream GitHub Pages workflow (`.github/workflows/deploy.yml`) was removed so this repository never deploys the upstream site; a `tests/` directory and an `npm test` script were added. SPDX headers (`Apache-2.0`, `2026 NguyenThong251 and xT contributors`) are added to source files as they are touched.
