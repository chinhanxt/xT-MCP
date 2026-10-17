// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
export async function* readLines(body: ReadableStream<Uint8Array>, maxBytes = 4 * 1024 * 1024, maxLine = 262144): AsyncGenerator<string> {
  const reader = body.getReader(),
    decoder = new TextDecoder('utf-8', { fatal: true });
  let buffer = '',
    bytes = 0,
    complete = false;
  try {
    for (;;) {
      const next = await reader.read();
      if (next.done) {
        buffer += decoder.decode();
        complete = true;
        break;
      }
      bytes += next.value.length;
      if (bytes > maxBytes) throw new Error('Response exceeds byte limit');
      buffer += decoder.decode(next.value, { stream: true });
      let index: number;
      while ((index = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, index).replace(/\r$/, '');
        if (line.length > maxLine) throw new Error('Frame too large');
        buffer = buffer.slice(index + 1);
        yield line;
      }
      if (buffer.length > maxLine) throw new Error('Frame too large');
    }
    if (buffer) yield buffer.replace(/\r$/, '');
  } finally {
    if (!complete) await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
export async function* sseFrames(body: ReadableStream<Uint8Array>): AsyncGenerator<{ event: string; data: string }> {
  let event = '',
    data: string[] = [];
  for await (const line of readLines(body)) {
    if (!line) {
      if (data.length) yield { event, data: data.join('\n') };
      event = '';
      data = [];
    } else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''));
    else if (line.startsWith('event:')) event = line.slice(6).trim();
  }
  if (data.length) yield { event, data: data.join('\n') };
}
