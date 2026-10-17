# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
# REUSE-IgnoreStart
"""scripts/check_spdx.sh: phần mở rộng .tsx/.mjs/.css, bỏ .claude, phủ bằng REUSE.toml."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_spdx.sh"
HEADER = "// SPDX-License-Identifier: Apache-2.0\n// SPDX-FileCopyrightText: 2026 xT contributors\n"


def run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), str(root)], capture_output=True, text=True, check=False
    )


@pytest.mark.parametrize("name", ["App.tsx", "build.mjs", "style.css", "main.ts"])
def test_headerless_file_fails(tmp_path: Path, name: str) -> None:
    (tmp_path / name).write_text("body{}\n")
    result = run(tmp_path)
    assert result.returncode == 1
    assert name in result.stdout


@pytest.mark.parametrize(
    ("name", "header"),
    [
        ("App.tsx", HEADER),
        ("build.mjs", HEADER),
        ("style.css", "/* SPDX-License-Identifier: Apache-2.0 */\n"),
    ],
)
def test_headed_file_passes(tmp_path: Path, name: str, header: str) -> None:
    (tmp_path / name).write_text(header + "x\n")
    result = run(tmp_path)
    assert result.returncode == 0, result.stdout
    assert result.stdout.startswith("OK: 1 files")


def test_claude_directory_is_ignored(tmp_path: Path) -> None:
    (tmp_path / ".claude" / "skills").mkdir(parents=True)
    (tmp_path / ".claude" / "skills" / "tool.py").write_text("print(1)\n")
    assert run(tmp_path).returncode == 0


def test_reuse_annotation_covers_only_listed_paths(tmp_path: Path) -> None:
    (tmp_path / "REUSE.toml").write_text(
        "# SPDX-License-Identifier: Apache-2.0\nversion = 1\n[[annotations]]\n"
        'path = ["imports/ext/src/**"]\n'
        'SPDX-FileCopyrightText = "x"\nSPDX-License-Identifier = "Apache-2.0"\n'
    )
    (tmp_path / "imports/ext/src/deep").mkdir(parents=True)
    (tmp_path / "imports/ext/src/deep/a.tsx").write_text("x\n")
    assert run(tmp_path).returncode == 0
    (tmp_path / "imports/ext/other.tsx").write_text("x\n")
    result = run(tmp_path)
    assert result.returncode == 1
    assert "other.tsx" in result.stdout
    assert "deep/a.tsx" not in result.stdout


def test_annotation_with_other_license_does_not_cover(tmp_path: Path) -> None:
    (tmp_path / "REUSE.toml").write_text(
        '# SPDX-License-Identifier: Apache-2.0\nversion = 1\n[[annotations]]\npath = ["**"]\n'
        'SPDX-FileCopyrightText = "x"\nSPDX-License-Identifier = "MIT"\n'
    )
    (tmp_path / "a.tsx").write_text("x\n")
    assert run(tmp_path).returncode == 1


def test_annotation_outside_imports_does_not_cover(tmp_path: Path) -> None:
    (tmp_path / "REUSE.toml").write_text(
        "# SPDX-License-Identifier: Apache-2.0\nversion = 1\n[[annotations]]\n"
        'path = ["docs/**", "seed/**"]\n'
        'SPDX-FileCopyrightText = "x"\nSPDX-License-Identifier = "Apache-2.0"\n'
    )
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "page.html").write_text("<p>x</p>\n")
    result = run(tmp_path)
    assert result.returncode == 1
    assert "page.html" in result.stdout
# REUSE-IgnoreEnd
