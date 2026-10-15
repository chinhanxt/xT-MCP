// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { spawn } from "node:child_process";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const sp = resolve(fileURLToPath(import.meta.url), "../evil_server.py");
const child = spawn("uv", ["run", "--no-project", "--with", "mcp==2.3.0", "--with", "uvicorn", "--with", "starlette", "python", sp], { stdio: "inherit" });
const stop = () => { child.kill("SIGTERM"); setTimeout(() => process.exit(0), 300); };
process.on("SIGTERM", stop);
process.on("SIGINT", stop);
child.on("exit", (c) => process.exit(c ?? 0));
