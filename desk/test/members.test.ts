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
type Call = { name: string; arguments: Record<string, unknown> };
const ok = (data: unknown): ToolResult => ({ content: [{ type: 'text', text: JSON.stringify({ ok: true, data }) }] });
const error = (code: string, message: string, details = {}): ToolResult => ({ isError: true, structuredContent: { ok: false, error: { code, message, details } }, content: [] });
const grant = { ref: 'access_grant:g1', revision: 3, values: { employee: 'e1', system: 'keycloak', role: 'employee', issued_on: '2026-10-17', revoked_on: null } };
const task = { ref: 'lifecycle_task:t1', kind: 'task', suggested_tool: 'transition_record', revision: 4, step: 'it', title: 'Cấp quyền SSO', to_states: ['done', 'skipped'] };
let root: Root | undefined, container: HTMLDivElement, mcp: McpConnection, tools: McpTool[];
async function render() { await act(async () => root!.render(createElement(Portal, { area: 'H', mcp, tools, sandboxUrl: new URL('https://sandbox.test'), onOpenLink: () => true, revealDpop: undefined }))); }
async function mount(handler: (call: Call) => Promise<ToolResult>, names = ['inspect_workspace', 'find_people', 'create_record', 'start_process', 'search_records', 'get_inbox', 'transition_record', 'update_record']) {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div'); document.body.append(container); root = createRoot(container);
  tools = names.map(name => ({ name, inputSchema: { type: 'object' } }));
  mcp = { client: { callTool: vi.fn(async (call: Call) => call.name === 'inspect_workspace' ? ok({ me: { ref: 'person:hr1', roles: ['hr', 'employee'] }, models: [] }) : call.name === 'find_people' ? ok({ people: [] }) : handler(call)) }, tools } as unknown as McpConnection;
  await render();
}
const panel = () => container.querySelector('[aria-label="Thành viên và quyền"]')!;
const calls = (name: string) => vi.mocked(mcp.client.callTool).mock.calls.map(c => c[0] as Call).filter(c => c.name === name);
async function click(text: string) { const button = Array.from(panel().querySelectorAll('button')).find(b => b.textContent === text); expect(button, text).toBeTruthy(); await act(async () => button!.click()); }
async function input(label: string, value: string) { const el = panel().querySelector<HTMLInputElement>(`[aria-label="${label}"]`)!; await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(el, value); el.dispatchEvent(new Event('input', { bubbles: true })); }); }
async function submit(label: string) { await act(async () => panel().querySelector(`[aria-label="${label}"]`)!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))); }
async function fillHR() { for (const [label, value] of Object.entries({ 'Mã nhân viên': 'NV-0015', 'Họ và tên': 'Lan', 'Chức danh': 'Kỹ sư', 'ID phòng ban': 'dep1', 'ID tài khoản nhân viên': 'user1', 'Mã hồ sơ': 'ON-2026-0015', 'Ngày hiệu lực': '2026-10-17' })) await input(label, value); }
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; document.body.innerHTML = ''; vi.restoreAllMocks(); });

it('creates employee, case and process in order; resumes start with the same key after timeout', async () => {
  let starts = 0;
  await mount(async call => {
    if (call.name === 'create_record') return ok({ record: { ref: call.arguments.model_id === 'employee' ? 'employee:e1' : 'lifecycle_case:c1' } });
    if (++starts === 1) throw new Error('Mất kết nối');
    return ok({ process_instance: 'process_instance:p1', state: 'running' });
  });
  expect(panel().textContent).toContain('hr, employee');
  await fillHR(); await submit('Tiếp nhận nhân viên');
  expect(panel().textContent).toContain('Mất kết nối'); expect(panel().textContent).not.toContain('Đã khởi động');
  await submit('Tiếp nhận nhân viên');
  expect(calls('create_record').map(c => c.arguments)).toEqual([
    { model_id: 'employee', values: { emp_no: 'NV-0015', full_name: 'Lan', job_title: 'Kỹ sư', department: 'dep1', user_id: 'user1' }, idempotency_key: expect.any(String) },
    { model_id: 'lifecycle_case', values: { code: 'ON-2026-0015', employee: 'e1', kind: 'onboarding', effective_on: '2026-10-17', initiated_by: 'hr1' }, idempotency_key: expect.any(String) },
  ]);
  expect(calls('start_process')[0].arguments).toEqual({ process_id: 'employee_onboarding', subject_ref: 'lifecycle_case:c1', parameters: { case_id: 'c1', employee_id: 'e1', employee_user: 'person:user1' }, idempotency_key: expect.any(String) });
  expect(calls('start_process')[1]).toEqual(calls('start_process')[0]);
  expect(panel().textContent).toContain('Đã khởi động');
});
it('preserves the employee and retries the same failed case without claiming success', async () => {
  let cases = 0;
  await mount(async call => call.arguments.model_id === 'employee' ? ok({ record: { ref: 'employee:e1' } }) : ++cases === 1 ? error('access_denied', 'Không được tạo hồ sơ') : ok({}));
  await fillHR(); await submit('Tiếp nhận nhân viên'); await submit('Tiếp nhận nhân viên');
  expect(calls('create_record')).toHaveLength(3); expect(calls('create_record')[2]).toEqual(calls('create_record')[1]);
  expect(calls('start_process')).toHaveLength(0); expect(panel().textContent).not.toContain('Đã khởi động');
});
it('records a grant without claiming effective roles, lists/revokes using the listed revision and stable retry', async () => {
  let updates = 0;
  await mount(async call => {
    if (call.name === 'create_record') return ok({ record: grant });
    if (call.name === 'search_records') return ok({ items: [grant, { ...grant, ref: 'access_grant:g2', values: { ...grant.values, revoked_on: '2026-10-18' } }], next_cursor: null });
    if (++updates === 1) throw new Error('Timeout');
    return ok({ record: { ...grant, revision: 4, values: { ...grant.values, revoked_on: '2026-10-18' } } });
  });
  await input('ID nhân viên được cấp', 'e1'); await input('Vai trò được cấp', 'employee'); await input('Ngày cấp', '2026-10-17'); await submit('Đề xuất cấp quyền');
  expect(calls('create_record')[0].arguments).toMatchObject({ model_id: 'access_grant', values: { employee: 'e1', system: 'keycloak', role: 'employee', issued_on: '2026-10-17' } });
  expect(panel().textContent).toContain('chỉ hiệu lực sau bước IT');
  await click('Tải quyền truy cập'); expect(calls('search_records')[0].arguments).toEqual({ model_id: 'access_grant', limit: 50 });
  expect(panel().textContent).toContain('Chưa thu hồi'); expect(panel().textContent).toContain('Đã thu hồi');
  await input('Ngày thu hồi', '2026-10-18'); await click('Thu hồi employee'); await click('Thu hồi employee');
  expect(calls('update_record')[0].arguments).toMatchObject({ ref: grant.ref, set: { revoked_on: '2026-10-18' }, expected_revision: 3, idempotency_key: expect.any(String) });
  expect(calls('update_record')[1]).toEqual(calls('update_record')[0]); expect(panel().querySelectorAll('button')).not.toContainEqual(expect.objectContaining({ textContent: 'Thu hồi employee' }));
});
it.each(['Vai trò không nằm trong danh sách được cấp', 'Người tạo không được tự duyệt cấp quyền'])('shows IT failure: %s', async message => {
  await mount(async call => call.name === 'get_inbox' ? ok({ items: [task], next_cursor: null }) : error('access_denied', message));
  await click('Tải bước quy trình'); await click('Hoàn thành IT'); await click('Hoàn thành IT');
  expect(calls('get_inbox')[0].arguments).toEqual({ kinds: ['task'], limit: 50 });
  expect(calls('transition_record')[0].arguments).toMatchObject({ ref: task.ref, to_state: 'done', expected_revision: 4, idempotency_key: expect.any(String) });
  expect(calls('transition_record')[1]).toEqual(calls('transition_record')[0]);
  expect(panel().querySelector('[data-state="forbidden"]')?.textContent).toContain(message); expect(panel().textContent).not.toContain('Đã hoàn thành');
});
it('offers human confirmation and retries the exact IT operation before reporting completion', async () => {
  let transitions = 0;
  await mount(async call => call.name === 'get_inbox' ? ok({ items: transitions > 1 ? [] : [task] }) : ++transitions === 1 ? error('confirmation_required', 'Cần xác nhận', { confirm_url: 'https://xt.test/desk/confirm/it' }) : ok({ record: { ref: task.ref }, to_state: 'done' }));
  await click('Tải bước quy trình'); await click('Hoàn thành IT');
  expect(panel().querySelector('a')?.href).toBe('https://xt.test/desk/confirm/it');
  await click('Hoàn thành IT'); expect(calls('transition_record')[1]).toEqual(calls('transition_record')[0]); expect(panel().textContent).toContain('Đã hoàn thành Cấp quyền SSO.'); expect(panel().textContent).not.toMatch(/lifecycle_task:|[0-9a-f]{8}-[0-9a-f]{4}/);
});
it('shows empty, loading and unavailable-tool states', async () => {
  let finish!: (r: ToolResult) => void;
  await mount(async () => new Promise(resolve => { finish = resolve; }));
  await click('Tải quyền truy cập'); expect(panel().textContent).toContain('Đang xử lý');
  await act(async () => finish(ok({ items: [], next_cursor: null }))); expect(panel().textContent).toContain('Chưa có quyền truy cập');
  tools = tools.filter(t => t.name !== 'get_inbox'); await render(); await click('Tải bước quy trình');
  expect(panel().querySelector('[data-state="forbidden"]')?.textContent).toContain('get_inbox');
});
it('stops a pending onboarding chain and clears personal state after session replacement', async () => {
  let finish!: (r: ToolResult) => void;
  await mount(async () => new Promise(resolve => { finish = resolve; }));
  await fillHR(); await submit('Tiếp nhận nhân viên'); const old = mcp;
  mcp = { client: { callTool: vi.fn(async call => call.name === 'inspect_workspace' ? ok({ me: { roles: ['employee'] }, models: [] }) : ok({ people: [] })) }, tools } as unknown as McpConnection;
  await render(); await act(async () => finish(ok({ record: { ref: 'employee:old' } })));
  expect(vi.mocked(old.client.callTool).mock.calls.filter(c => c[0].name === 'create_record')).toHaveLength(1);
  expect(panel().textContent).not.toContain('Đã khởi động'); expect(panel().querySelector<HTMLInputElement>('[aria-label="Họ và tên"]')?.value).toBe(''); expect(panel().textContent).not.toContain('hr, employee');
});
it('preserves spaces while typing and retires grant keys only after validated success', async () => {
  let n = 0;
  await mount(async () => ++n === 1 ? error('internal_error', 'Timeout') : ok({ record: grant }));
  await input('Họ và tên', 'Nguyễn '); expect(panel().querySelector<HTMLInputElement>('[aria-label="Họ và tên"]')!.value).toBe('Nguyễn ');
  async function fillGrant() { await input('ID nhân viên được cấp', 'e1'); await input('Vai trò được cấp', 'employee'); await input('Ngày cấp', '2026-10-17'); }
  await fillGrant(); await submit('Đề xuất cấp quyền'); await submit('Đề xuất cấp quyền');
  await fillGrant(); await submit('Đề xuất cấp quyền');
  expect(calls('create_record')[1]).toEqual(calls('create_record')[0]);
  expect(calls('create_record')[2].arguments.idempotency_key).not.toBe(calls('create_record')[1].arguments.idempotency_key);
});
it('clears records and blocks further calls on expiry until the session is replaced', async () => {
  await mount(async call => call.name === 'search_records' ? ok({ items: [grant] }) : error('unauthenticated', 'Tài khoản đã bị thu hồi'));
  await click('Tải quyền truy cập'); await click('Tải bước quy trình');
  expect(panel().querySelector('[data-state="expired"]')?.textContent).toContain('Tài khoản đã bị thu hồi');
  expect(panel().textContent).not.toContain('access_grant:g1'); expect(panel().textContent).not.toContain('hr, employee');
  expect(Array.from(panel().querySelectorAll('button')).every(b => b.disabled)).toBe(true);
});
it('preflights the HR tools and rejects malformed process success', async () => {
  await mount(async call => call.name === 'create_record' ? ok({ record: { ref: call.arguments.model_id === 'employee' ? 'employee:e1' : 'lifecycle_case:c1' } }) : ok({ process_instance: '', state: 'running' }), ['inspect_workspace', 'find_people', 'create_record']);
  await fillHR(); await submit('Tiếp nhận nhân viên'); expect(calls('create_record')).toHaveLength(0);
  tools = [...tools, { name: 'start_process', inputSchema: { type: 'object' } }]; await render(); await submit('Tiếp nhận nhân viên');
  expect(panel().textContent).toContain('Chưa xác nhận được quy trình'); expect(panel().textContent).not.toContain('Đã khởi động');
});
it('paginates grants and inbox without completing unrelated tasks or exposing unsafe confirmation URLs', async () => {
  await mount(async call => {
    if (call.name === 'search_records') return ok({ items: call.arguments.cursor ? [{ ...grant, ref: 'access_grant:g2' }] : [grant], next_cursor: call.arguments.cursor ? null : 'g-next' });
    if (call.name === 'get_inbox') return ok({ items: call.arguments.cursor ? [task] : [{ ref: 'task:other', step: 'it' }], next_cursor: call.arguments.cursor ? null : 't-next' });
    return error('confirmation_required', 'Cần xác nhận', { confirm_url: 'https://evil.test/desk/confirm/it' });
  });
  await click('Tải quyền truy cập'); await click('Tải thêm quyền'); expect(calls('search_records')[1].arguments.cursor).toBe('g-next');
  await click('Tải bước quy trình'); expect(panel().textContent).not.toContain('Hoàn thành IT');
  await click('Tải thêm bước'); expect(calls('get_inbox')[1].arguments.cursor).toBe('t-next'); await click('Hoàn thành IT');
  expect(panel().querySelector('a')).toBeNull(); expect(panel().textContent).not.toContain('Đã hoàn thành');
});
it('shows the actual generic workflow failure and the approval restrictions without inventing a cause', async () => {
  await mount(async call => call.name === 'get_inbox' ? ok({ items: [task] }) : error('internal_error', 'Quy trình gặp sự cố; quản trị đã được ghi nhận.'));
  await click('Tải bước quy trình'); await click('Hoàn thành IT');
  expect(panel().querySelector('[role="alert"]')?.textContent).toContain('Quy trình gặp sự cố');
  expect(panel().textContent).toContain('máy chủ có thể chỉ trả lỗi quy trình tổng quát'); expect(panel().textContent).not.toContain('Đã hoàn thành');
});
it.each([
  ['role_not_allowed', 'Vai trò không nằm trong danh sách được phép cấp.'],
  ['self_approval', 'Người duyệt phải khác người đề nghị, người được cấp quyền và người tạo bản ghi cấp quyền.'],
])('shows safe grant details for %s and keeps the failed IT step retryable', async (reason, message) => {
  await mount(async call => call.name === 'get_inbox' ? ok({ items: [task] }) : error('precondition_failed', 'Không thể hoàn thành bước.', { reason, message, trace_id: 'grant-trace' }));
  await click('Tải bước quy trình'); await click('Hoàn thành IT');
  expect(panel().querySelector('[role="alert"]')?.textContent).toContain(message);
  expect(panel().querySelector('[role="alert"]')?.textContent).toContain('grant-trace');
  expect(panel().textContent).not.toContain('máy chủ có thể chỉ trả lỗi quy trình tổng quát');
  expect(panel().textContent).not.toContain('Đã hoàn thành');
  await click('Hoàn thành IT'); expect(calls('transition_record')[1]).toEqual(calls('transition_record')[0]);
});
it.each([undefined, '', '   ', 42, { text: 'invalid' }])('keeps generic workflow guidance for malformed details.message: %s', async message => {
  await mount(async call => call.name === 'get_inbox' ? ok({ items: [task] }) : error('precondition_failed', 'Quy trình gặp sự cố; quản trị đã được ghi nhận.', { reason: 'role_not_allowed', message }));
  await click('Tải bước quy trình'); await click('Hoàn thành IT');
  expect(panel().querySelector('[role="alert"]')?.textContent).toContain('Quy trình gặp sự cố');
  expect(panel().textContent).toContain('máy chủ có thể chỉ trả lỗi quy trình tổng quát');
});
it.each([
  ['internal_error', { reason: 'role_not_allowed', message: 'secret database details' }],
  ['precondition_failed', { message: 'secret database details' }],
])('keeps unrecognized %s details private', async (code, details) => {
  await mount(async call => call.name === 'get_inbox' ? ok({ items: [task] }) : error(code, 'Quy trình gặp sự cố; quản trị đã được ghi nhận.', details));
  await click('Tải bước quy trình'); await click('Hoàn thành IT');
  expect(panel().querySelector('[role="alert"]')?.textContent).toContain('Quy trình gặp sự cố');
  expect(panel().textContent).not.toContain('secret');
  expect(panel().textContent).toContain('máy chủ có thể chỉ trả lỗi quy trình tổng quát');
});
it('removes cached effective roles when inspect_workspace refresh is forbidden', async () => {
  await mount(async () => ok({})); expect(panel().textContent).toContain('hr, employee');
  vi.mocked(mcp.client.callTool).mockResolvedValue(error('access_denied', 'Không thể kiểm tra vai trò') as Awaited<ReturnType<typeof mcp.client.callTool>>);
  await click('Làm mới vai trò'); expect(panel().textContent).not.toContain('hr, employee');
  expect(container.querySelector('[data-state="forbidden"]')?.textContent).toContain('Không thể kiểm tra vai trò');
});
it('allows correcting a rejected case while keeping the created employee locked', async () => {
  await mount(async call => call.name === 'start_process' ? ok({ process_instance: 'process_instance:p1', state: 'running' }) : call.arguments.model_id === 'employee' ? ok({ record: { ref: 'employee:e1' } }) : (call.arguments.values as Record<string, unknown>).code === 'ON-2026-0015' ? error('rule_violation', 'Mã hồ sơ đã tồn tại') : ok({ record: { ref: 'lifecycle_case:c1' } }));
  await fillHR(); await submit('Tiếp nhận nhân viên');
  expect(panel().querySelector('[aria-label="Họ và tên"]')!.closest('fieldset')!.disabled).toBe(true);
  expect(panel().querySelector('[aria-label="Mã hồ sơ"]')!.closest('fieldset')!.disabled).toBe(false);
  await input('Mã hồ sơ', 'ON-2026-0020'); await submit('Tiếp nhận nhân viên');
  expect(calls('create_record').filter(c => c.arguments.model_id === 'employee')).toHaveLength(1);
  expect(panel().textContent).toContain('Đã khởi động');
});

it('names the revoke button after the grant, keeping the technical ref only as tooltip and description', async () => {
  await mount(async call => call.name === 'search_records' ? ok({ items: [grant], next_cursor: null }) : ok({}));
  await click('Tải quyền truy cập');
  const button = Array.from(panel().querySelectorAll('button')).find(b => b.textContent === 'Thu hồi employee')!;
  expect(button.textContent).not.toContain('access_grant:');
  expect(button.getAttribute('title')).toBe('access_grant:g1');
  expect(panel().querySelector(`[id="${button.getAttribute('aria-describedby')}"]`)?.textContent).toContain('access_grant:g1');
});
it('shows the grant hint only for grant and IT-step errors, not for other operations', async () => {
  const hint = 'Nếu bước cấp quyền thất bại';
  await mount(async call => call.name === 'create_record' || call.name === 'transition_record' ? error('internal', 'Lỗi quy trình') : call.name === 'get_inbox' ? ok({ items: [task], next_cursor: null }) : error('internal', 'Lỗi khác'));
  await input('ID nhân viên được cấp', 'e1'); await input('Vai trò được cấp', 'employee'); await input('Ngày cấp', '2026-10-17'); await submit('Đề xuất cấp quyền');
  expect(panel().textContent).toContain(hint);
  await click('Tải bước quy trình'); expect(panel().textContent).not.toContain(hint);
  await click('Hoàn thành IT'); expect(panel().textContent).toContain(hint);
  await click('Tải quyền truy cập'); expect(panel().textContent).toContain('Lỗi khác'); expect(panel().textContent).not.toContain(hint);
});
it('does not attach the grant hint to a session-expired alert, even right after a grant attempt', async () => {
  await mount(async () => error('unauthenticated', 'Phiên hết hạn'));
  await input('ID nhân viên được cấp', 'e1'); await input('Vai trò được cấp', 'employee'); await input('Ngày cấp', '2026-10-17'); await submit('Đề xuất cấp quyền');
  expect(panel().querySelector('[data-state="expired"]')?.textContent).toContain('Phiên hết hạn');
  expect(panel().textContent).not.toContain('Nếu bước cấp quyền thất bại');
});
it('shows confirmation_required as a neutral "Cần xác nhận" status with the confirm link, not an alert', async () => {
  await mount(async call => call.name === 'get_inbox' ? ok({ items: [task], next_cursor: null }) : error('confirmation_required', 'Chờ người phụ trách', { confirm_url: 'https://xt.test/desk/confirm/it' }));
  await click('Tải bước quy trình'); await click('Hoàn thành IT');
  const note = panel().querySelector('[data-state="confirm"]')!;
  expect(note.getAttribute('role')).toBe('status'); expect(note.className).toContain('portal-confirm');
  expect(note.textContent).toContain('Cần xác nhận'); expect(note.querySelector('a')?.getAttribute('href')).toBe('https://xt.test/desk/confirm/it');
  expect(panel().querySelector('[data-state="error"]')).toBeNull();
});
