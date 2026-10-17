import { useState } from 'react';
import type { Model, Knowledge, StudioApp, McpConnector } from '../contracts.ts';
import { Modal, ErrorBox } from './ui.tsx';
import { api } from './api.ts';
export function ImportApp({
  models,
  knowledge,
  connectors,
  close,
  done,
}: {
  models: Model[];
  knowledge: Knowledge[];
  connectors: McpConnector[];
  close: () => void;
  done: (a: StudioApp) => void;
}) {
  const [manifest, setManifest] = useState<any>(null),
    [error, setError] = useState(''),
    [modelMap, setModels] = useState<Record<string, string>>({}),
    [knowledgeMap, setKnowledge] = useState<Record<string, string>>({}),
    [connectorMap, setConnectors] = useState<Record<string, string>>({}),
    [busy, setBusy] = useState(false);
  const connectorDeps = Array.isArray(manifest?.dependencies?.connectors) ? manifest.dependencies.connectors : [];
  const modelDeps = Array.isArray(manifest?.dependencies?.models) ? manifest.dependencies.models : [],
    knowledgeDeps = Array.isArray(manifest?.dependencies?.knowledge) ? manifest.dependencies.knowledge : [];
  return (
    <Modal title="Nhập ứng dụng" close={close}>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setError('');
          try {
            done(
              await api('/apps/import', 'POST', {
                manifest,
                bindings: { models: modelMap, knowledge: knowledgeMap, connectors: connectorMap },
              }),
            );
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <label>
          File cấu hình xT Studio (.json)
          <input
            type="file"
            accept="application/json,.json"
            required
            onChange={async (e) => {
              setError('');
              setManifest(null);
              const file = e.target.files?.[0];
              if (!file) return;
              try {
                if (file.size > 1500000) throw new Error('File vượt quá 1.5 MB.');
                const value = JSON.parse(await file.text());
                if (value?.format !== 'xt-studio.app' || value.version !== 1 || !value.app)
                  throw new Error('Định dạng ứng dụng không được hỗ trợ.');
                setManifest(value);
                setModels({});
                setKnowledge({});
                setConnectors({});
              } catch (e) {
                setError((e as Error).message);
              }
            }}
          />
        </label>
        {manifest && (
          <>
            <h3>{String(manifest.app.name)}</h3>
            <p className="hint">Chọn model và kho kiến thức trong workspace này. File không chứa API key hoặc tài liệu.</p>
            {connectorDeps.map((dep: any) => (
              <label key={String(dep.ref)}>
                MCP: {String(dep.name)}
                <select
                  required
                  value={connectorMap[dep.ref] ?? (connectors.some((c) => c.id === dep.ref) ? dep.ref : '')}
                  onChange={(e) => setConnectors({ ...connectorMap, [dep.ref]: e.target.value })}
                >
                  <option value="">Chọn MCP tương ứng</option>
                  {connectors
                    .filter((c) => c.enabled)
                    .map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name}
                      </option>
                    ))}
                </select>
              </label>
            ))}
            {modelDeps.map((dep: any) => (
              <label key={String(dep.ref)}>
                Model: {String(dep.name)}
                <select
                  required
                  value={modelMap[dep.ref] ?? (models.some((m) => m.id === dep.ref) ? dep.ref : '')}
                  onChange={(e) => setModels({ ...modelMap, [dep.ref]: e.target.value })}
                >
                  <option value="">Chọn model tương ứng</option>
                  {models
                    .filter((m) => m.kind === dep.kind)
                    .map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.name}
                      </option>
                    ))}
                </select>
              </label>
            ))}
            {knowledgeDeps.map((dep: any) => (
              <label key={String(dep.ref)}>
                Kiến thức: {String(dep.name)}
                <select
                  required
                  value={knowledgeMap[dep.ref] ?? (knowledge.some((k) => k.id === dep.ref) ? dep.ref : '')}
                  onChange={(e) => setKnowledge({ ...knowledgeMap, [dep.ref]: e.target.value })}
                >
                  <option value="">Chọn kho tương ứng</option>
                  {knowledge.map((k) => (
                    <option key={k.id} value={k.id}>
                      {k.name}
                    </option>
                  ))}
                </select>
              </label>
            ))}
          </>
        )}
        <ErrorBox error={error} />
        <button className="primary full" disabled={!manifest || busy}>
          {busy ? 'Đang nhập…' : 'Nhập thành ứng dụng mới'}
        </button>
      </form>
    </Modal>
  );
}
