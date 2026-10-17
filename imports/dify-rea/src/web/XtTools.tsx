import { useEffect, useState } from 'react';
import type { AppConfig } from '../contracts.ts';
import type { XtTool } from '../xt-tools.ts';
import { api } from './api.ts';

export function XtTools({ config, patch }: { config: AppConfig; patch: (value: Partial<AppConfig>) => void }) {
  const [tools, setTools] = useState<XtTool[]>([]), [error, setError] = useState('');
  useEffect(() => {
    const abort = new AbortController();
    api<{ tools: XtTool[] }>('/xt/tools', 'GET', undefined, abort.signal)
      .then((value) => setTools(value.tools))
      .catch((cause) => { if (!abort.signal.aborted) setError(cause.message); });
    return () => abort.abort();
  }, []);
  return <fieldset><legend>Công cụ doanh nghiệp xT</legend>
    <p>Chạy theo quyền người sử dụng. Thao tác ghi tuân theo quy trình và xác nhận của xT.</p>
    {error && <p role="alert">{error}</p>}
    {tools.map((tool) => <label className="check" key={tool.name} title={tool.description}>
      <input type="checkbox" checked={(config.xt_tools ?? []).includes(tool.name)} onChange={(event) => patch({
        xt_tools: event.target.checked ? [...(config.xt_tools ?? []), tool.name] : (config.xt_tools ?? []).filter((name) => name !== tool.name),
      })} />{tool.name}
    </label>)}
  </fieldset>;
}
