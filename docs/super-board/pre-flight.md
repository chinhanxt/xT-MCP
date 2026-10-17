<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->

Halt gate: every item must be [✓] before `super-board run`. Observed 2026-10-17.

🔑 Credentials the loop will need:
- [✓] gh authenticated as chinhanxt, scopes include `project` and `repo` (`gh auth status`)
- [✓] None required for unit/fixture verification
Info (not a checkbox): heavy integration/live ChatGPT tests need the local Keycloak/Postgres stack and `~/.config/xt/` secrets. Workers must NOT read them.

🛠  Tools the loop will need:
- [✓] git 2.43.0 (`git --version`)
- [✓] uv 0.11.25 (`uv --version`)
- [✓] Python 3.13.14 satisfies requires-python >=3.12 (`uv run python --version`)
- [✓] node v24.14.1 (`node --version`)
- [✓] npm 11.11.0 (`npm --version`)
- [✓] claude 2.1.299 (`claude --version`)
- [✓] docker server 29.1.3, containers not started (`docker info --format '{{.ServerVersion}}'`)
- [✓] Playwright chromium-1243 and chromium-1248 present (`ls ~/.cache/ms-playwright`)
- [✓] ruff 0.17.0 (`uv run ruff --version`)

🌐 Environment:
- [✓] RAM available 6 GB of 15 GB, threshold >= 4 GB (`free -g`)
- [✓] Disk free 16G on /, threshold >= 10G (`df -h /`)
- [✓] Remote reachable, origin staging at 58504850 (`git -C /home/chinhan/xT ls-remote --heads origin staging`)
