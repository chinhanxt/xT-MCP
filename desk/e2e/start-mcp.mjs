// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Chép research/spike-mcp-apps vào thư mục tạm, đổi cổng, chạy server MCP. Dọn khi thoát.
import { spawn } from "node:child_process";
import { cpSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const port = process.env.E2E_MCP_PORT;
if (!port) throw new Error("E2E_MCP_PORT chưa đặt");
const src = resolve(fileURLToPath(import.meta.url), "../../../research/spike-mcp-apps");
const root = join(tmpdir(), `xt-desk-e2e-${port}`);
rmSync(root, { recursive: true, force: true });
mkdirSync(root, { recursive: true });
const dir = join(root, "spike-mcp-apps");
cpSync(src, dir, { recursive: true });
const sp = join(dir, "server.py");
writeFileSync(sp, readFileSync(sp, "utf8").replace("port=18200", `port=${port}`));

const child = spawn(
  "uv",
  ["run", "--no-project", "--with", "mcp==2.3.0", "--with", "uvicorn", "--with", "starlette", "python", sp],
  { stdio: "inherit" },
);
const stop = () => {
  child.kill("SIGTERM");
  setTimeout(() => {
    rmSync(root, { recursive: true, force: true });
    process.exit(0);
  }, 500);
};
process.on("SIGTERM", stop);
process.on("SIGINT", stop);
child.on("exit", (c) => process.exit(c ?? 0));
