import type { ChatMessage, GenerationOptions, Model } from './contracts.ts';
import type { FunctionTool } from './models.ts';
export function wireRequest(model: Model, messages: ChatMessage[], tools: FunctionTool[], options: GenerationOptions, stream: boolean) {
  const clean = messages.map(({ citations: _citations, ...m }) => m);
  if (model.provider === 'anthropic') {
    const converted: { role: 'user' | 'assistant'; content: Record<string, unknown>[] }[] = [];
    for (const message of clean.filter((m) => m.role !== 'system')) {
      const role = message.role === 'assistant' ? 'assistant' : 'user';
      const content: Record<string, unknown>[] =
        message.role === 'tool'
          ? [{ type: 'tool_result', tool_use_id: message.tool_call_id, content: message.content ?? '' }]
          : [
              ...(message.content ? [{ type: 'text', text: message.content }] : []),
              ...(message.tool_calls?.map((t) => ({
                type: 'tool_use',
                id: t.id,
                name: t.function.name,
                input: JSON.parse(t.function.arguments),
              })) ?? []),
            ];
      if (converted.at(-1)?.role === role) converted.at(-1)!.content.push(...content);
      else converted.push({ role, content });
    }
    return {
      path: '/messages',
      body: {
        model: model.model,
        system:
          clean
            .filter((m) => m.role === 'system')
            .map((m) => m.content)
            .join('\n') + (options.json_mode ? '\nReturn only valid JSON.' : ''),
        messages: converted,
        max_tokens: options.max_tokens,
        temperature: options.temperature,
        stream,
        ...(tools.length
          ? {
              tools: tools.map((t) => ({
                name: t.function.name,
                description: t.function.description,
                input_schema: t.function.parameters,
              })),
            }
          : {}),
      },
    };
  }
  const native = model.provider === 'ollama';
  return {
    path: native ? '/api/chat' : '/chat/completions',
    body: {
      model: model.model,
      messages: clean.map((m) =>
        native && m.tool_calls
          ? {
              ...m,
              tool_calls: m.tool_calls.map((t) => ({ ...t, function: { ...t.function, arguments: JSON.parse(t.function.arguments) } })),
            }
          : m,
      ),
      stream,
      ...(tools.length ? { tools } : {}),
      ...(native
        ? {
            think: false,
            options: { num_predict: options.max_tokens, temperature: options.temperature },
            ...(options.json_mode ? { format: 'json' } : {}),
          }
        : {
            max_tokens: options.max_tokens,
            temperature: options.temperature,
            ...(options.json_mode ? { response_format: { type: 'json_object' } } : {}),
          }),
    },
  };
}
