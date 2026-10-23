# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Nhúng icon Lucide (ISC) dùng trong src/desk.html thành sprite <symbol>, ghi ra index.html."""
import re, sys, pathlib
here = pathlib.Path(__file__).resolve().parent
icons = pathlib.Path(sys.argv[1])  # thư mục lucide-static/icons
html = (here / "desk.html").read_text()
names = sorted(set(re.findall(r'href="#i-([a-z0-9-]+)"', html)))
syms = []
for n in names:
    svg = (icons / f"{n}.svg").read_text()
    inner = re.search(r"<svg[^>]*>(.*)</svg>", svg, re.S).group(1).strip()
    syms.append(f'<symbol id="i-{n}" viewBox="0 0 24 24">{inner}</symbol>')
sprite = ('<!-- icons: Lucide, SPDX-License-Identifier: ISC, (c) Lucide Contributors, https://lucide.dev -->\n<svg width="0" height="0" style="position:absolute" aria-hidden="true">'
          + "".join(syms) + "</svg>")
(here.parent / "index.html").write_text(html.replace("<!--ICONS-->", sprite))
print(len(names), "icons")
