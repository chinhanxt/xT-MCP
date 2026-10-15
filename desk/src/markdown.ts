// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { marked } from "marked";
import DOMPurify from "dompurify";

// Token PII của xT (docs/tool-contracts-v1.md §3.8): [PII:<token>]
const PII_RE = /\[PII:[A-Za-z0-9_-]+\]/g;

let hooked = false;
function ensureHooks(): void {
  if (hooked) return;
  hooked = true;
  DOMPurify.addHook("afterSanitizeAttributes", (node) => {
    if (node.tagName === "A") {
      node.setAttribute("target", "_blank");
      node.setAttribute("rel", "noopener noreferrer nofollow");
    }
  });
}

/** Markdown của LLM là dữ liệu không tin cậy: render rồi sanitize. Không cho ảnh, form, script, style; chỉ giữ `href`
 *  (không class/style/id/title) để LLM không giả giao diện Desk. Nhãn PII dựng bằng DOM sau khi sanitize. */
export function renderMarkdown(src: string): string {
  ensureHooks();
  const raw = marked.parse(src, { async: false, gfm: true, breaks: true }) as string;
  const frag = DOMPurify.sanitize(raw, {
    ALLOWED_TAGS: [
      "p", "br", "strong", "em", "del", "code", "pre", "blockquote", "ul", "ol", "li", "a",
      "h1", "h2", "h3", "h4", "h5", "h6", "hr", "table", "thead", "tbody", "tr", "th", "td",
    ],
    ALLOWED_ATTR: ["href", "target", "rel"],
    ALLOW_DATA_ATTR: false,
    ALLOWED_URI_REGEXP: /^(?:https?|mailto):/i,
    RETURN_DOM_FRAGMENT: true,
  }) as DocumentFragment;
  const doc = frag.ownerDocument;
  const walker = doc.createTreeWalker(frag, 4 /* NodeFilter.SHOW_TEXT */);
  const texts: Text[] = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode()) texts.push(n as Text);
  for (const t of texts) {
    const v = t.data;
    if (!v.includes("[PII:")) continue;
    const out = doc.createDocumentFragment();
    let last = 0;
    for (const m of v.matchAll(PII_RE)) {
      out.append(v.slice(last, m.index));
      const span = doc.createElement("span");
      span.setAttribute("class", "pii");
      span.textContent = "[đã che]";
      out.append(span);
      last = (m.index ?? 0) + m[0].length;
    }
    out.append(v.slice(last));
    t.replaceWith(out);
  }
  const div = doc.createElement("div");
  div.append(frag);
  return div.innerHTML;
}
