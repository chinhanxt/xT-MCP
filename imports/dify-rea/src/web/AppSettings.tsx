import type { AppConfig, StudioApp, Model, Knowledge, McpConnector } from '../contracts.ts';
import { defaultGeneration } from '../contracts.ts';
import { api } from './api.ts';
import { McpTools } from './McpConnectors.tsx';
import { GenerationSettings, InputDefinitions } from './GenerationSettings.tsx';
import { XtTools } from './XtTools.tsx';
export function AppSettings({
  app,
  config,
  models,
  knowledge,
  connectors,
  name,
  description,
  patch,
  setName,
  setDescription,
  back,
  onError,
}: {
  app: StudioApp;
  config: AppConfig;
  models: Model[];
  knowledge: Knowledge[];
  connectors: McpConnector[];
  name: string;
  description: string;
  patch: (v: Partial<AppConfig>) => void;
  setName: (v: string) => void;
  setDescription: (v: string) => void;
  back: () => void;
  onError: (v: string) => void;
}) {
  return (
    <>
      <GenerationSettings value={config.generation ?? defaultGeneration()} onChange={(generation) => patch({ generation })} />
      {app.kind === 'workflow' && <InputDefinitions fields={config.inputs ?? []} onChange={(inputs) => patch({ inputs })} />}
      <>
        <label>
          Model chat
          <select value={config.model_id} onChange={(e) => patch({ model_id: e.target.value })}>
            <option value="">Chọn model</option>
            {models
              .filter((m) => m.kind === 'chat')
              .map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name} · {m.model}
                </option>
              ))}
          </select>
        </label>
        <label>
          Hướng dẫn hệ thống
          <textarea rows={7} value={config.system_prompt} onChange={(e) => patch({ system_prompt: e.target.value })} />
        </label>
        <label>Kiến thức</label>
        {knowledge.length ? (
          <div className="knowledge-checks">
            {knowledge.map((k) => (
              <label className="check" key={k.id}>
                <input
                  type="checkbox"
                  checked={config.knowledge_ids.includes(k.id)}
                  onChange={(e) =>
                    patch({
                      knowledge_ids: e.target.checked ? [...config.knowledge_ids, k.id] : config.knowledge_ids.filter((id) => id !== k.id),
                    })
                  }
                />
                {k.name}
              </label>
            ))}
          </div>
        ) : (
          <p className="hint">Tạo kho kiến thức tại mục Kiến thức để đính kèm tài liệu.</p>
        )}
        {app.kind === 'agent' && (
          <>
            <label>Công cụ được phép</label>
            {(['knowledge_search', 'calculator'] as const).map((tool) => (
              <label className="check" key={tool}>
                <input
                  type="checkbox"
                  checked={config.tools.includes(tool)}
                  onChange={(e) => patch({ tools: e.target.checked ? [...config.tools, tool] : config.tools.filter((t) => t !== tool) })}
                />
                {tool === 'knowledge_search' ? 'Tìm trong kiến thức' : 'Tính toán số học'}
              </label>
            ))}
            <McpTools connectors={connectors} config={config} patch={patch} />
            {new URLSearchParams(location.search).get('integrated') === '1' && <XtTools config={config} patch={patch} />}
            <label>
              Giới hạn bước
              <input
                type="number"
                min={1}
                max={8}
                value={config.max_steps}
                onChange={(e) => patch({ max_steps: Number(e.target.value) })}
              />
            </label>
          </>
        )}
        <details className="app-settings">
          <summary>Thông tin ứng dụng</summary>
          <label>
            Tên
            <input value={name} onChange={(e) => setName(e.target.value)} maxLength={100} />
          </label>
          <label>
            Mô tả
            <textarea value={description} rows={2} onChange={(e) => setDescription(e.target.value)} />
          </label>
          <button
            className="danger"
            onClick={async () => {
              if (!confirm('Xóa ứng dụng và hội thoại của ứng dụng?')) return;
              try {
                await api(`/apps/${app.id}`, 'DELETE');
                back();
              } catch (e) {
                onError((e as Error).message);
              }
            }}
          >
            Xóa ứng dụng
          </button>
        </details>
      </>
    </>
  );
}
