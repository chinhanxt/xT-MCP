// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import type { GenerationOptions, InputField } from '../contracts.ts';
export function GenerationSettings({ value, onChange }: { value: GenerationOptions; onChange: (v: GenerationOptions) => void }) {
  return (
    <details className="app-settings">
      <summary>Tham số model</summary>
      <label>
        Temperature
        <input
          type="number"
          min={0}
          max={2}
          step={0.1}
          value={value.temperature}
          onChange={(e) => onChange({ ...value, temperature: Number(e.target.value) })}
        />
      </label>
      <label>
        Giới hạn token phản hồi
        <input
          type="number"
          min={1}
          max={16384}
          value={value.max_tokens}
          onChange={(e) => onChange({ ...value, max_tokens: Number(e.target.value) })}
        />
      </label>
      <label>
        Số tin nhắn ngữ cảnh
        <input
          type="number"
          min={1}
          max={100}
          value={value.context_messages}
          onChange={(e) => onChange({ ...value, context_messages: Number(e.target.value) })}
        />
      </label>
      <label className="check">
        <input type="checkbox" checked={value.json_mode} onChange={(e) => onChange({ ...value, json_mode: e.target.checked })} />
        Yêu cầu JSON hợp lệ
      </label>
    </details>
  );
}
export function InputDefinitions({ fields, onChange }: { fields: InputField[]; onChange: (v: InputField[]) => void }) {
  const patch = (index: number, value: Partial<InputField>) => onChange(fields.map((f, i) => (i === index ? { ...f, ...value } : f)));
  return (
    <section className="input-definitions">
      <h2>Đầu vào workflow</h2>
      <p className="hint">Dùng {'{{ten_bien}}'} trong prompt và template. Biến input vẫn dùng cho ô văn bản chung.</p>
      {fields.map((field, i) => (
        <div className="input-definition" key={i}>
          <div className="form-grid">
            <label>
              Tên biến
              <input value={field.name} onChange={(e) => patch(i, { name: e.target.value })} />
            </label>
            <label>
              Nhãn
              <input value={field.label} onChange={(e) => patch(i, { label: e.target.value })} />
            </label>
          </div>
          <label>
            Kiểu
            <select
              value={field.type}
              onChange={(e) =>
                patch(i, {
                  type: e.target.value as InputField['type'],
                  ...(e.target.value === 'boolean' ? { default: false } : { default: undefined }),
                  ...(e.target.value === 'select' ? { options: ['Lựa chọn 1'] } : {}),
                })
              }
            >
              <option value="text">Văn bản</option>
              <option value="number">Số</option>
              <option value="boolean">Có / Không</option>
              <option value="select">Danh sách lựa chọn</option>
            </select>
          </label>
          {field.type === 'select' && (
            <label>
              Lựa chọn, mỗi dòng một giá trị
              <textarea
                rows={3}
                value={(field.options ?? []).join('\n')}
                onChange={(e) => patch(i, { options: e.target.value.split('\n') })}
              />
            </label>
          )}
          <label className="check">
            <input type="checkbox" checked={field.required} onChange={(e) => patch(i, { required: e.target.checked })} />
            Bắt buộc
          </label>
          <button className="danger" onClick={() => onChange(fields.filter((_, index) => index !== i))}>
            Xóa trường
          </button>
        </div>
      ))}
      <button
        onClick={() => onChange([...fields, { name: 'field_' + (fields.length + 1), label: 'Trường mới', type: 'text', required: true }])}
      >
        + Thêm trường
      </button>
    </section>
  );
}
