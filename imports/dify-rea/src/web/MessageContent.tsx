// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
export function MessageContent({ content }: { content: string }) {
  return (
    <div className="markdown">
      <Markdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={{
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          ),
          img: ({ alt, src }) => (
            <a href={src} target="_blank" rel="noopener noreferrer">
              [Ảnh: {alt || 'Mở ảnh'}]
            </a>
          ),
        }}
      >
        {content}
      </Markdown>
    </div>
  );
}
