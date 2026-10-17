// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Render only a canonical, header-safe origin; never interpolate arbitrary env text in nginx.
import { readFileSync, writeFileSync } from "node:fs";

const raw = process.env.DESK_XT_ORIGIN;
if (!raw) throw new Error("DESK_XT_ORIGIN is required for the sandbox frame policy");
const url = new URL(raw);
if (url.username || url.password || !/^https?:\/\/(?:[A-Za-z0-9.-]+|\[[0-9a-f:]+\])(?::[0-9]{1,5})?$/i.test(url.origin)) {
  throw new Error("DESK_XT_ORIGIN must be a header-safe HTTP(S) origin");
}
const template = readFileSync(new URL("../nginx.conf", import.meta.url), "utf8");
writeFileSync(process.argv[2] || "nginx.rendered.conf", template.replaceAll("@@DESK_ORIGIN@@", url.origin));
