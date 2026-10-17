// @vitest-environment jsdom
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import type { McpConnection } from '../src/mcp';
import type { McpTool, ToolResult } from '../src/types';
vi.mock('../src/config', () => ({ config: { xtOrigin: 'https://xt.test', appGlobalCallsPerSec: 10, appGlobalBurst: 10 } }));
import { Portal } from '../src/Portal';
import { buildValues, fieldErrorsOf, readFields, validate } from '../src/portal-request';

type Call = { name: string; arguments: Record<string, unknown> };
const ok = (data: unknown): ToolResult => ({ content: [{ type: 'text', text: JSON.stringify({ ok: true, data }) }] });
const err = (code: string, message: string, details: unknown = {}): ToolResult => ({ isError: true, content: [{ type: 'text', text: JSON.stringify({ ok: false, error: { code, message, details } }) }] });
const model = { id: 'advance_request', label: 'Tạm ứng', fields: [
  { id: 'purpose', label: 'Mục đích', type: 'string', required: true },
  { id: 'amount', label: 'Số tiền', type: 'money', required: true },
  { id: 'trip_start', label: 'Ngày đi', type: 'date' },
  { id: 'kind', label: 'Loại', type: 'enum', enum_values: ['trip', 'other'] },
  { id: 'approver', label: 'Người duyệt', type: 'person' },
] };
const workspace = { me: { name: 'Lan' }, models: [{ id: 'advance_request', label: 'Tạm ứng', primitive: 'request' }, { id: 'doc', label: 'Tài liệu', primitive: 'document' }], processes: [] };
let root: Root | undefined;
let container: HTMLDivElement;
let mcp: McpConnection;
async function mount(caller: (call: Call) => Promise<ToolResult>, names = ['inspect_workspace', 'describe_model', 'get_inbox', 'create_record', 'submit_request', 'get_record']) {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div'); document.body.append(container);
  const tools = names.map((name) => ({ name, inputSchema: { type: 'object' } })) as McpTool[];
  mcp = { client: { callTool: vi.fn(caller) }, tools } as unknown as McpConnection;
  root = createRoot(container);
  await act(async () => { root!.render(createElement(Portal, { area: 'P', mcp, tools, sandboxUrl: new URL('https://sandbox.test'), onOpenLink: () => true, revealDpop: undefined })); });
}
const calls = (name: string) => vi.mocked(mcp.client.callTool).mock.calls.map((c) => c[0] as Call).filter((c) => c.name === name);
async function fill(label: string, value: string) {
  const el = container.querySelector<HTMLInputElement | HTMLSelectElement>(`#req-${label}`)!;
  const proto = el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  await act(async () => { Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(el, value); el.dispatchEvent(new Event(el instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true })); });
}
const send = () => act(async () => { container.querySelector('.portal-request form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); });
const base = (extra: (call: Call) => ToolResult | undefined) => async (call: Call): Promise<ToolResult> => {
  const special = extra(call); if (special) return special;
  if (call.name === 'inspect_workspace') return ok(workspace);
  if (call.name === 'describe_model') return ok({ model });
  if (call.name === 'get_inbox') return ok({ items: [], next_cursor: null });
  throw new Error(`unexpected ${call.name}`);
};
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; document.body.innerHTML = ''; vi.restoreAllMocks(); });

it('pure helpers: fields from describe_model, string money, enum validation, field errors', () => {
  const fields = readFields(model);
  expect(validate(fields, {})).toEqual({ purpose: 'Trường bắt buộc', amount: 'Trường bắt buộc' });
  expect(validate(fields, { purpose: 'x', amount: '1.5.2' }).amount).toContain('số');
  expect(validate(fields, { purpose: 'x', amount: '8000000', kind: 'bogus' }).kind).toBe('Giá trị không hợp lệ');
  expect(buildValues(fields, { purpose: ' đi công tác ', amount: '8000000.50', approver: 'person:1' })).toEqual({ purpose: 'đi công tác', amount: '8000000.50' });
  expect(fieldErrorsOf(err('rule_violation', 'sai', { violations: [{ field: 'amount', message: 'Tối đa 30.000.000' }] }))).toEqual({ amount: 'Tối đa 30.000.000' });
  expect(fieldErrorsOf(err('invalid_request', 'x', { fields: [{ field: 'purpose' }] }))).toEqual({ purpose: 'x' });
});

it('builds the form only for request models, blocks empty required fields and sends create_record then submit_request', async () => {
  await mount(base((call) => {
    if (call.name === 'create_record') return ok({ record: { ref: 'advance_request:1', state: 'initial', revision: 1 } });
    if (call.name === 'submit_request') return ok({ request: { ref: 'advance_request:1', state: 'in_review', revision: 2 }, route: [] });
  }));
  const select = container.querySelector<HTMLSelectElement>('#req-model')!;
  expect(Array.from(select.options).map((o) => o.textContent)).toEqual(['Tạm ứng']);
  expect(calls('describe_model')[0].arguments).toEqual({ model_id: 'advance_request' });
  expect(container.querySelector('label[for="req-purpose"]')?.textContent).toBe('Mục đích *');
  expect(container.querySelector('#req-approver')?.textContent).toContain('có thể bỏ qua');
  await send();
  expect(container.querySelector('#req-purpose-err')?.textContent).toBe('Trường bắt buộc');
  expect(container.querySelector('#req-purpose')?.getAttribute('aria-invalid')).toBe('true');
  expect(calls('create_record')).toHaveLength(0);
  await fill('purpose', 'Đi Đà Nẵng'); await fill('amount', '8000000'); await fill('kind', 'trip');
  await send();
  expect(calls('create_record')[0].arguments).toEqual({ model_id: 'advance_request', values: { purpose: 'Đi Đà Nẵng', amount: '8000000', kind: 'trip' }, idempotency_key: expect.any(String) });
  expect(calls('submit_request')[0].arguments).toEqual({ ref: 'advance_request:1', expected_revision: 1, idempotency_key: expect.any(String) });
  expect(container.querySelector('[data-testid="request-done"]')?.textContent).toBe('Đã gửi duyệt Tạm ứng.');
  expect((container.querySelector('#req-purpose') as HTMLInputElement).value).toBe('');
});

it('shows backend field errors inline, and a corrected resubmit gets a new idempotency key', async () => {
  let n = 0;
  await mount(base((call) => {
    if (call.name === 'create_record') return ++n === 1 ? err('rule_violation', 'Vượt hạn mức', { violations: [{ field: 'amount', message: 'Tối đa 30.000.000' }] }) : ok({ record: { ref: 'advance_request:2', revision: 1 } });
    if (call.name === 'submit_request') return ok({ request: { ref: 'advance_request:2', state: 'in_review', revision: 2 } });
  }));
  await fill('purpose', 'x'); await fill('amount', '99000000'); await send();
  expect(container.querySelector('#req-amount-err')?.textContent).toBe('Tối đa 30.000.000');
  expect(container.querySelector('[role="alert"].portal-error')?.textContent).toContain('Vượt hạn mức');
  expect(calls('submit_request')).toHaveLength(0);
  await fill('amount', '20000000'); await send();
  const keys = calls('create_record').map((c) => c.arguments.idempotency_key);
  expect(keys[0]).not.toBe(keys[1]);
  expect(container.querySelector('#req-amount-err')).toBeNull();
});

it('retries a failed submit with the same draft and idempotency keys and never creates a second draft', async () => {
  let submits = 0;
  await mount(base((call) => {
    if (call.name === 'create_record') return ok({ record: { ref: 'advance_request:3' } });
    if (call.name === 'get_record') return ok({ record: { ref: 'advance_request:3', revision: 4 } });
    if (call.name === 'submit_request') return ++submits === 1 ? err('confirmation_required', 'Cần xác nhận', { confirm_url: 'https://xt.test/desk/confirm/c1', confirmation_id: 'c1' }) : ok({ request: { ref: 'advance_request:3', state: 'in_review', revision: 5 } });
  }));
  await fill('purpose', 'x'); await fill('amount', '1000'); await send();
  const alert = container.querySelector('[role="status"].portal-confirm')!;
  expect(alert.textContent).toContain('Phiếu nháp Tạm ứng đã tạo nhưng chưa gửi duyệt');
  expect(alert.querySelector('a')?.getAttribute('href')).toBe('https://xt.test/desk/confirm/c1');
  expect(container.querySelector<HTMLInputElement>('#req-purpose')!.disabled).toBe(true);
  const retry = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Gửi lại')!;
  await act(async () => retry.click());
  expect(calls('create_record')).toHaveLength(1);
  const [a, b] = calls('submit_request');
  expect(b.arguments).toEqual(a.arguments);
  expect(a.arguments.expected_revision).toBe(4);
  expect(container.querySelector('[data-testid="request-done"]')).toBeTruthy();
});

it('ignores a second submit while the first is in flight and reuses one create call', async () => {
  let release!: (value: ToolResult) => void;
  await mount(base((call) => {
    if (call.name === 'create_record') return undefined;
    if (call.name === 'submit_request') return ok({ request: { ref: 'advance_request:5', revision: 2 } });
  }));
  const original = mcp.client.callTool as unknown as ReturnType<typeof vi.fn>;
  const fallback = original.getMockImplementation()! as (c: Call) => Promise<ToolResult>;
  original.mockImplementation(async (call: Call) => call.name === 'create_record' ? new Promise<ToolResult>((resolve) => { release = resolve; }) : fallback(call));
  await fill('purpose', 'x'); await fill('amount', '5'); await send(); await send();
  expect(calls('create_record')).toHaveLength(1);
  await act(async () => release(ok({ record: { ref: 'advance_request:5', revision: 1 } })));
  expect(calls('submit_request')).toHaveLength(1);
});

it('shows empty, forbidden and backend-error states honestly', async () => {
  await mount(async (call) => call.name === 'inspect_workspace' ? ok({ me: {}, models: [{ id: 'doc', primitive: 'document' }] }) : ok({ items: [] }));
  expect(container.querySelector('.portal-request')?.textContent).toContain('chưa có loại yêu cầu');
  await act(async () => root!.unmount()); root = undefined; document.body.innerHTML = '';
  await mount(base((call) => call.name === 'describe_model' ? err('access_denied', 'Không có quyền model.read') : undefined));
  expect(container.querySelector('.portal-request [role="alert"]')?.textContent).toContain('Không có quyền model.read');
  expect(container.querySelector<HTMLButtonElement>('.portal-request form button')!.disabled).toBe(true);
  await act(async () => root!.unmount()); root = undefined; document.body.innerHTML = '';
  await mount(base(() => undefined), ['inspect_workspace', 'describe_model', 'get_inbox']);
  await fill('purpose', 'x'); await fill('amount', '5'); await send();
  expect(container.querySelector('.portal-request [role="alert"]')?.textContent).toContain('Thiếu quyền');
});

it('blocks submit when a required field type has no picker yet', async () => {
  const saved = model.fields[4];
  Object.assign(saved, { required: true });
  try {
    await mount(base(() => undefined));
    expect(container.querySelector<HTMLButtonElement>('.portal-request form button')!.disabled).toBe(true);
    expect(container.querySelector('#req-approver')?.textContent).toContain('Trường bắt buộc nhưng chưa nhập được');
  } finally { Object.assign(saved, { required: false }); }
});

it('labels enum options from describe_model options and falls back to the raw value without a label', async () => {
  const withOptions = { ...model, fields: model.fields.map((f) => f.id === 'kind' ? { ...f, enum_values: undefined, options: [{ value: 'trip', label: 'Công tác' }, { value: 'other', label: 'Khác' }] } : f) };
  await mount(base((call) => call.name === 'describe_model' ? ok({ model: withOptions }) : undefined));
  const select = container.querySelector<HTMLSelectElement>('#req-kind')!;
  expect(Array.from(select.options).map((o) => [o.value, o.textContent])).toEqual([['', 'Chọn'], ['trip', 'Công tác'], ['other', 'Khác']]);
  expect(readFields({ fields: [{ id: 'p', type: 'enum', enum_values: ['zz'] }] })[0].enumLabels).toEqual({});
});

it('offers a picker for ref fields via search_records and sends the bare id', async () => {
  model.fields.push({ id: 'project', label: 'Dự án', type: 'ref', ref_model: 'project' } as never);
  try {
    await mount(base((call) => {
      if (call.name === 'search_records') return ok({ items: [{ ref: 'project:p1', title: 'Cổng thông tin' }] });
      if (call.name === 'create_record') return ok({ record: { ref: 'advance_request:7', revision: 1 } });
      if (call.name === 'submit_request') return ok({ request: { ref: 'advance_request:7', revision: 2 } });
    }), ['inspect_workspace', 'describe_model', 'get_inbox', 'create_record', 'submit_request', 'get_record', 'search_records']);
    expect(calls('search_records')[0].arguments).toEqual({ model_id: 'project', limit: 100 });
    const select = container.querySelector<HTMLSelectElement>('#req-project')!;
    expect(select.tagName).toBe('SELECT');
    expect(Array.from(select.options).map((o) => o.textContent)).toEqual(['Chọn dự án', 'Cổng thông tin']);
    expect(container.textContent).not.toContain('“ref”');
    await fill('purpose', 'x'); await fill('amount', '5'); await fill('project', 'p1'); await send();
    expect(calls('create_record')[0].arguments.values).toMatchObject({ project: 'p1' });
  } finally { model.fields.pop(); }
});
