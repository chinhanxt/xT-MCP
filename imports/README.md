# Imported projects

- `dify-rea/`: xT Studio reconstruction project copied from `projects/reclaw/repos/dify-rea`.
- `waretrack-3d/`: WareTrack 3D, selected as the replacement warehouse visualization. The unified build serves it at `/waretrack/`.

The Studio copy excludes dependencies, build output, runtime data, logs and Git metadata. WareTrack 3D retains the supplied project's metadata. Install each project's dependencies from its own `package-lock.json`.

WareTrack 3D currently uses simulated data. Its `npm test` (Node built-in test runner, no extra dependency) covers the data layer (`src/data`, `src/state/store.js`); the 3D scene and simulation loop are verified only by the production build. The previous `waretrack/` copy has been removed at the project owner's request.

## waretrack-3d: provenance and license

- Upstream: <https://github.com/NguyenThong251/waretrack-3d>
- Imported commit: `14b8edc`
- License status: **chưa xác định — cần xin phép tác giả trước khi phát hành.** The upstream repository has no LICENSE file; no license is assumed or granted here. Do not publish or redistribute this module (including release tarballs) until the author's permission or a license is recorded in this section.
- Local changes: the upstream GitHub Pages workflow (`.github/workflows/deploy.yml`) was removed so this repository never deploys the upstream site; a `tests/` directory and an `npm test` script were added. Other files under `waretrack-3d/` keep their upstream form (no SPDX headers).
