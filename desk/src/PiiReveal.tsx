// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { useEffect, useMemo, useState } from "react";
import type { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { config } from "./config";
import { createRevealStore, REVEAL_TOOLTIP, REVEAL_TTL_MS, revealTokens, type RevealDeps, type RevealOutcome } from "./pii";
import type { ToolResult } from "./types";
import { Eye, EyeOff, Info } from "lucide-react";

/** Nút "Hiện" cho token PII trong kết quả tool. Giá trị thật chỉ ở state của component này (không vào history LLM/log). */
export function PiiReveal({ tokens, client, dpop }: { tokens: string[]; client: Client; dpop?: () => RevealDeps["dpop"] }) {
  const store = useMemo(() => createRevealStore(), []);
  const [shown, setShown] = useState<RevealOutcome | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const un = store.subscribe(() => setShown(store.get()));
    const off = store.clearOnNavigation(window);
    return () => {
      un();
      off();
      store.clear();
    };
  }, [store]);

  useEffect(() => {
    if (!shown) return;
    const t = setTimeout(() => store.clear(), REVEAL_TTL_MS);
    return () => clearTimeout(t);
  }, [shown, store]);

  async function reveal() {
    setBusy(true);
    setErr(null);
    try {
      store.set(
        await revealTokens(tokens, {
          callTool: (p) => client.callTool(p) as Promise<ToolResult>,
          fetch: (...a) => fetch(...a),
          xtOrigin: config.xtOrigin,
          dpop: dpop?.(),
        }),
      );
    } catch (e) {
      setErr(e instanceof Error ? e.message : "LỖI");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="pii-reveal" data-testid="pii-reveal">
      {shown ? (
        <>
          <ul data-testid="pii-values">
            {Object.entries(shown.values).map(([t, v]) => (
              <li key={t}>
                <code>{t}</code>: <span className="pii-value">{v}</span>
              </li>
            ))}
            {shown.denied.map((t) => (
              <li key={t}>
                <code>{t}</code>: <em>bạn không có quyền xem</em>
              </li>
            ))}
          </ul>
          <button onClick={() => store.clear()}>Ẩn<EyeOff aria-hidden="true" /></button>
        </>
      ) : (
        <button disabled={busy} onClick={() => void reveal()} title={REVEAL_TOOLTIP} aria-label={`Hiện giá trị thật. ${REVEAL_TOOLTIP}`}>
          Hiện<Eye aria-hidden="true" />
        </button>
      )}
      <span className="hint pii-hint" title={REVEAL_TOOLTIP}><Info aria-hidden="true" /> Chỉ hiển thị cho bạn, không gửi cho AI</span>
      {err && <span className="note" role="alert"> Không xem được ({err}).</span>}
    </div>
  );
}
