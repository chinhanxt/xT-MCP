import { useState } from 'react';
import type { FormEvent } from 'react';
import type { McpConnector, AppConfig } from '../contracts.ts';
import { api } from './api.ts';
import { ErrorBox, Modal } from './ui.tsx';
export function McpConnectors({
  items,
  reload,
  notify,
}: {
  items: McpConnector[];
  reload: () => Promise<void>;
  notify: (value: string) => void;
}) {
  const [editing, setEditing] = useState<McpConnector | 'new' | null>(null),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  async function perform(work: () => Promise<unknown>, message: string) {
    setError('');
    setBusy(true);
    try {
      await work();
      await reload();
      notify(message);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget),
      value: Record<string, string> = { name: String(form.get('name')), url: String(form.get('url')) };
    const token = String(form.get('token') ?? '');
    if (token || form.get('clear_token')) value.token = token;
    await perform(async () => {
      await api('/mcp' + (editing && editing !== 'new' ? '/' + editing.id : ''), editing === 'new' ? 'POST' : 'PUT', value);
      setEditing(null);
    }, 'Đã kết nối MCP');
  }
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">CÔNG CỤ</span>
          <h1>Kết nối MCP</h1>
          <p>Kết nối server công cụ và chọn quyền sử dụng trong từng Agent.</p>
        </div>
        <button
          className="primary"
          disabled={busy}
          onClick={() => {
            setError('');
            setEditing('new');
          }}
        >
          + Thêm MCP
        </button>
      </div>
      {!editing && <ErrorBox error={error} />}
      {!items.length && <p className="hint">Thêm URL Streamable HTTP của MCP server. Hỗ trợ server cục bộ và Bearer token.</p>}
      <div className="app-grid">
        {items.map((item) => (
          <article className="app-card" key={item.id}>
            <div className="row spread">
              <h3>{item.name}</h3>
              <span className={'pill ' + (item.enabled ? 'green' : '')}>{item.enabled ? 'Đang bật' : 'Đã tắt'}</span>
            </div>
            <p>{item.url}</p>
            <small>
              {item.tools.length} công cụ · {item.has_key ? 'Có token' : 'Không dùng token'}
            </small>
            <details>
              <summary>Danh sách công cụ</summary>
              {item.tools.map((tool) => (
                <p key={tool.name}>
                  <strong>{tool.name}</strong>
                  <br />
                  {tool.description}
                </p>
              ))}
            </details>
            <div className="row wrap">
              <button
                disabled={busy}
                onClick={() => {
                  setError('');
                  setEditing(item);
                }}
              >
                Sửa
              </button>
              <button disabled={busy} onClick={() => perform(() => api('/mcp/' + item.id + '/refresh', 'POST', {}), 'Đã cập nhật công cụ')}>
                Kiểm tra & cập nhật
              </button>
              <button
                disabled={busy}
                onClick={() =>
                  perform(() => api('/mcp/' + item.id, 'PATCH', { enabled: !item.enabled }), item.enabled ? 'Đã tắt MCP' : 'Đã bật MCP')
                }
              >
                {item.enabled ? 'Tắt' : 'Bật'}
              </button>
              <button
                className="danger"
                disabled={busy}
                onClick={() => {
                  if (confirm('Xóa MCP connector này?')) perform(() => api('/mcp/' + item.id, 'DELETE'), 'Đã xóa MCP');
                }}
              >
                Xóa
              </button>
            </div>
          </article>
        ))}
      </div>
      {editing && (
        <Modal
          title={editing === 'new' ? 'Kết nối MCP server' : 'Sửa MCP server'}
          close={() => {
            if (!busy) setEditing(null);
          }}
        >
          <form onSubmit={save}>
            <label>
              Tên
              <input name="name" required maxLength={100} defaultValue={editing === 'new' ? '' : editing.name} />
            </label>
            <label>
              URL Streamable HTTP
              <input
                name="url"
                type="url"
                required
                placeholder="http://127.0.0.1:8000/mcp"
                defaultValue={editing === 'new' ? '' : editing.url}
              />
            </label>
            <label>
              Bearer token
              <input
                name="token"
                type="password"
                maxLength={4000}
                autoComplete="off"
                placeholder={editing !== 'new' && editing.has_key ? 'Để trống để giữ token đã lưu' : 'Không bắt buộc'}
              />
            </label>
            {editing !== 'new' && editing.has_key && (
              <label className="check">
                <input type="checkbox" name="clear_token" />
                Xóa token đã lưu
              </label>
            )}
            <p className="hint">
              Kết nối và lấy danh sách công cụ trước khi lưu. Khi schema thay đổi, chọn lại công cụ trong Agent rồi publish lại.
            </p>
            <ErrorBox error={error} />
            <button className="primary full" disabled={busy}>
              {busy ? 'Đang kết nối…' : 'Kết nối & lưu'}
            </button>
          </form>
        </Modal>
      )}
    </>
  );
}
export function McpTools({
  connectors,
  config,
  patch,
}: {
  connectors: McpConnector[];
  config: AppConfig;
  patch: (value: Partial<AppConfig>) => void;
}) {
  const selected = config.mcp_tools ?? [];
  return (
    <>
      <label>Công cụ từ MCP</label>
      {!connectors.length && <p className="hint">Thêm server tại mục Kết nối MCP.</p>}
      {connectors.map((source) => (
        <details key={source.id} open>
          <summary>
            {source.name}
            {!source.enabled && ' · Đã tắt'}
          </summary>
          {source.tools.map((tool) => {
            const old = selected.find((t) => t.connector_id === source.id && t.tool_name === tool.name);
            return (
              <label className="check" key={tool.name}>
                <input
                  type="checkbox"
                  disabled={!source.enabled && !old}
                  checked={!!old && old.schema_hash === tool.schema_hash}
                  onChange={(e) =>
                    patch({
                      mcp_tools: [
                        ...selected.filter((t) => !(t.connector_id === source.id && t.tool_name === tool.name)),
                        ...(e.target.checked ? [{ connector_id: source.id, tool_name: tool.name, schema_hash: tool.schema_hash }] : []),
                      ],
                    })
                  }
                />
                {tool.name}
                {old && old.schema_hash !== tool.schema_hash && ' · Schema mới, chọn lại'}
              </label>
            );
          })}
        </details>
      ))}
      {selected
        .filter((t) => !connectors.some((c) => c.id === t.connector_id && c.tools.some((tool) => tool.name === t.tool_name)))
        .map((t) => (
          <div className="row" key={t.connector_id + t.tool_name}>
            <span>Tool không còn tồn tại: {t.tool_name}</span>
            <button onClick={() => patch({ mcp_tools: selected.filter((x) => x !== t) })}>Gỡ</button>
          </div>
        ))}
    </>
  );
}
