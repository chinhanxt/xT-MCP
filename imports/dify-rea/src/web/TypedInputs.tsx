import type { InputField } from '../contracts.ts';
export function TypedInputs({
  fields,
  values,
  onChange,
  disabled = false,
}: {
  fields: InputField[];
  values: Record<string, string | number | boolean>;
  onChange: (v: Record<string, string | number | boolean>) => void;
  disabled?: boolean;
}) {
  return (
    <div className="typed-inputs">
      {fields.map((field) => (
        <label key={field.name}>
          {field.label}
          {field.required && ' *'}
          {field.type === 'boolean' ? (
            <input
              type="checkbox"
              disabled={disabled}
              checked={Boolean(values[field.name] ?? field.default ?? false)}
              onChange={(e) => onChange({ ...values, [field.name]: e.target.checked })}
            />
          ) : field.type === 'select' ? (
            <select
              disabled={disabled}
              required={field.required}
              value={String(values[field.name] ?? field.default ?? '')}
              onChange={(e) => onChange({ ...values, [field.name]: e.target.value })}
            >
              <option value="">Chọn giá trị</option>
              {field.options?.map((option) => (
                <option key={option}>{option}</option>
              ))}
            </select>
          ) : (
            <input
              disabled={disabled}
              required={field.required}
              type={field.type === 'number' ? 'number' : 'text'}
              step="any"
              value={String(values[field.name] ?? field.default ?? '')}
              onChange={(e) => {
                const updated = { ...values };
                if (e.target.value === '') delete updated[field.name];
                else updated[field.name] = field.type === 'number' ? Number(e.target.value) : e.target.value;
                onChange(updated);
              }}
            />
          )}
        </label>
      ))}
    </div>
  );
}
