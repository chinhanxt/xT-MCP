import type { InputField } from '../contracts.ts';
export function formInputValues(fields: InputField[], values: Record<string, string | number | boolean>) {
  return Object.fromEntries(
    fields.flatMap((field) => {
      const value = values[field.name] ?? field.default ?? (field.type === 'boolean' ? false : undefined);
      return value === undefined ? [] : [[field.name, value]];
    }),
  );
}
