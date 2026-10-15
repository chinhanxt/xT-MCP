// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { useEffect, useMemo, useRef, useState } from "react";
import { AppRenderer } from "@mcp-ui/client";
import type { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { createAppBridgeHandlers, createGlobalToolLimiter, createRateLimiter, sanitizeUiCsp, type UiCsp } from "./bridge";
import { config } from "./config";
import { confirmInfo } from "./confirm";
import { PiiReveal } from "./PiiReveal";
import { extractTokens } from "./pii";
import type { McpTool, ToolResult } from "./types";

export interface ToolEntry {
  id: string;
  name: string;
  args: Record<string, unknown>;
  status: "running" | "ok" | "error";
  result?: ToolResult;
  resourceUri?: string; // _meta.ui.resourceUri của tool
}

interface Props {
  entry: ToolEntry;
  client: Client;
  allTools: McpTool[];
  sandboxUrl: URL;
  onOpenLink: (url: string, frame: Element | null) => boolean;
}

// Hằng số cố định: AppRenderer tạo lại bridge nếu identity của prop này đổi giữa các lần render.
const HOST_CAPS = { serverTools: {}, serverResources: {}, openLinks: {} };

// Rate limit chung theo tên tool cho mọi instance App của phiên (ngoài rate limit riêng từng instance)
const globalLimiter = createGlobalToolLimiter({ ratePerSec: config.appGlobalCallsPerSec, burst: config.appGlobalBurst });

const STATUS_VI = { running: "đang chạy", ok: "xong", error: "lỗi" } as const;

interface UiResource {
  html: string;
  csp: UiCsp;
}

/** Một instance App: tự đọc resource ui:// (lấy html + _meta.ui.csp), rate limit và allowlist tool riêng. */
function AppFrame({ entry, client, allTools, sandboxUrl, onOpenLink }: Props) {
  const [height, setHeight] = useState(380);
  const frameRef = useRef<HTMLDivElement>(null);
  const [res, setRes] = useState<UiResource | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const handlers = useMemo(
    () =>
      createAppBridgeHandlers({
        tools: allTools,
        callTool: (p) => client.callTool(p) as Promise<ToolResult>,
        readResource: (p) => client.readResource(p),
        globalLimiter,
        maxArgsBytes: config.appMaxArgsBytes,
        limiter: createRateLimiter({ ratePerSec: config.appCallsPerSec, burst: config.appCallBurst, maxInflight: config.appMaxInflight }),
      }),
    [allTools, client],
  );

  useEffect(() => {
    let dead = false;
    client
      .readResource({ uri: entry.resourceUri! })
      .then((r) => {
        const c = r.contents[0] as { text?: string; _meta?: { ui?: { csp?: UiCsp } } } | undefined;
        if (!c || typeof c.text !== "string") throw new Error("resource ui:// không có HTML");
        if (!dead) setRes({ html: c.text, csp: sanitizeUiCsp(c._meta?.ui?.csp, config.xtOrigin) });
      })
      .catch((e: Error) => !dead && setErr(e.message));
    return () => {
      dead = true;
    };
  }, [client, entry.resourceUri]);

  if (err) return <div className="note">Không nạp được ứng dụng: {err}</div>;
  if (!res) return null;
  return (
    <div className="app-frame" style={{ height }} ref={frameRef}>
      <AppRenderer
        // KHÔNG truyền client: AppBridge.connect() sẽ ghi đè mọi onCallTool/onReadResource bằng proxy thẳng tới server.
        // Mọi yêu cầu của App đi qua `handlers` (chính sách của host).
        toolName={entry.name}
        hostCapabilities={HOST_CAPS}
        html={res.html}
        sandbox={{ url: sandboxUrl, csp: res.csp }}
        toolInput={entry.args}
        toolResult={entry.result as never}
        onCallTool={handlers.onCallTool as never}
        onReadResource={handlers.onReadResource as never}
        onListResources={handlers.onListResources as never}
        onListResourceTemplates={handlers.onListResourceTemplates as never}
        onListPrompts={handlers.onListPrompts as never}
        onSizeChanged={({ height: h }) => h && setHeight(Math.min(Math.max(h, 120), 720))}
        onOpenLink={async ({ url }) => {
          return { isError: !onOpenLink(url, frameRef.current) };
        }}
        onError={(e) => console.error("AppRenderer", e)}
      />
    </div>
  );
}

export function ToolItem(props: Props) {
  const { entry } = props;
  const [open, setOpen] = useState(false);
  const confirm = confirmInfo(entry.result, config.xtOrigin);
  const piiTokens = entry.status === "ok" ? extractTokens(entry.result) : [];
  return (
    <div className="tool" data-testid="tool-item">
      <button className={`chip ${entry.status}`} onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="chip-name">{entry.name}</span>
        <span className="chip-status">{STATUS_VI[entry.status]}</span>
      </button>
      {open && (
        <pre className="tool-detail">
          {JSON.stringify({ arguments: entry.args, result: entry.result?.structuredContent ?? entry.result?.content }, null, 2)}
        </pre>
      )}
      {piiTokens.length > 0 && <PiiReveal tokens={piiTokens} client={props.client} />}
      {confirm && (
        <div className="confirm-card" data-testid="confirm-card">
          <div>Cần người dùng xác nhận{confirm.summary ? `: ${confirm.summary}` : "."}</div>
          <a href={confirm.url} target="_blank" rel="noopener noreferrer">
            Mở trang xác nhận
          </a>
        </div>
      )}
      {entry.resourceUri && entry.status !== "running" && entry.result && <AppFrame {...props} />}
    </div>
  );
}
