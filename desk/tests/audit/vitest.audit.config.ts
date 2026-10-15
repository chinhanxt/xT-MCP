// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Chạy test audit: npx vitest run -c tests/audit/vitest.audit.config.ts
import { defineConfig } from "vitest/config";

export default defineConfig({ test: { root: ".", environment: "node", include: ["tests/audit/**/*.test.ts"] } });
