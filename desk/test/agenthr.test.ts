// @vitest-environment jsdom
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Nhân sự AI (Con người): danh sách, hồ sơ, tuyển, đình chỉ, và ẩn hẳn khi gói nhan-su-ai chưa cài. MCP giả lập theo hợp đồng AgentHR.
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import type { McpConnection } from '../src/mcp';
import type { McpTool, ToolResult } from '../src/types';
vi.mock('../src/config', () => ({ config: { xtOrigin: 'https://xt.test', appGlobalCallsPerSec: 10, appGlobalBurst: 10 } }));
import { Portal } from '../src/Portal';
import { activeJd, agentErrorText, capChips, capabilities, dateVi, flatCapabilities, jdFor, money, permitCovers, permitLabel, riskFlags, workload } from '../src/agenthr';
import { ToolError } from '../src/portal-result';

type Call = { name: string; arguments: Record<string, unknown> };
type Data = Record<string, unknown>;
const ok = (data: unknown): ToolResult => ({ content: [{ type: 'text', text: JSON.stringify({ ok: true, data }) }] });
const fail = (code: string, message: string, details?: Record<string, unknown>): ToolResult => ({ isError: true, structuredContent: { ok: false, error: { code, message, ...(details ? { details } : {}) } }, content: [] });
// Dạng phẳng của lõi (23/10); số trả về dạng chuỗi số, han_muc_gia_tri là tiền nguyên.
const caps = [{ model: 'stock_balance', read: true, create: false, update: false, submit: false }, { model: 'stock_request', read: true, create: true, update: true, submit: true }];
const legacyCaps = [{ model: 'stock_balance', thao_tac: ['read'] }, { model: 'stock_request', thao_tac: 'read,create' }];
const models = [{ id: 'ho_so_agent', label: 'Hồ sơ agent', primitive: 'record' }, { id: 'jd_agent', label: 'Mô tả công việc agent', primitive: 'request' }, { id: 'stock_request', label: 'Phiếu nhập/xuất', primitive: 'request' }, { id: 'stock_balance', label: 'Tồn kho', primitive: 'record' }];
const role = { ref: 'vai_tro_agent:r1', title: 'Trợ lý kho', values: { ma: 'kho', ten: 'Trợ lý kho', mo_ta: 'Tra tồn, soạn phiếu nhập xuất.', nang_luc: caps, han_muc_viec_ngay: '50', han_muc_gia_tri: 5000000, khung_gio: '08:00-18:00', ngay_lam: 'mon-fri', muc_tu_chu: '1' } };
const agent1 = { ref: 'ho_so_agent:a1', revision: 4, values: { ten: 'Trợ lý kho', client_id: 'chatgpt', nguoi_phu_trach: 'p1', vai_tro: 'r1', trang_thai: 'dang_lam', so_viec_hom_nay: '42', ngay_dem: '2026-10-23', jd_hieu_luc: 'j1' } };
const agent2 = { ref: 'ho_so_agent:a2', revision: 2, values: { ten: 'Trợ lý CSKH', client_id: 'claude', nguoi_phu_trach: { ref: 'person:p2', name: 'Lê Ngọc Trâm' }, vai_tro: { ref: 'vai_tro_agent:r2', name: 'Chăm sóc khách hàng' }, trang_thai: 'tam_dung', ly_do_tam_dung: 'han_muc', so_viec_hom_nay: '30' } };
const jd1 = { ref: 'jd_agent:j1', state: 'approved', updated_at: '2026-10-20T08:00:00Z', values: { ho_so: 'a1', nang_luc: caps, muc_tu_chu: '1', han_muc_viec_ngay: '50', han_muc_gia_tri: 5000000, khung_gio: '08:00-18:00', ngay_lam: 'mon-fri' } };
const jd2 = { ref: 'jd_agent:j2', state: 'approved', values: { ho_so: { ref: 'ho_so_agent:a2' }, nang_luc: JSON.stringify(legacyCaps), han_muc_viec_ngay: '30', han_muc_gia_tri: 0 } };
const incident = { ref: 'su_co_agent:s1', values: { ho_so: 'a1', loai: 'vuot_gia_tri', chi_tiet: 'Phiếu 50.000.000 ₫, hạn mức 5.000.000 ₫', trang_thai: 'mo' } };

let root: Root | undefined, container: HTMLDivElement, mcp: McpConnection;
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; document.body.innerHTML = ''; vi.restoreAllMocks(); });
async function mount(handler: (call: Call) => ToolResult | Promise<ToolResult> | undefined, catalog: Data[] = models) {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div'); document.body.append(container); root = createRoot(container);
  const tools = ['inspect_workspace', 'find_people', 'search_records', 'get_record', 'get_inbox', 'get_history', 'create_record', 'update_record', 'submit_request', 'decide_request'].map(name => ({ name, inputSchema: { type: 'object' } })) as McpTool[];
  mcp = { client: { callTool: vi.fn(async (call: Call) => {
    const custom = await handler(call); if (custom) return custom;
    if (call.name === 'inspect_workspace') return ok({ me: { ref: 'person:p1', name: 'Trần Văn Dũng', roles: ['manager'] }, models: catalog, processes: [] });
    if (call.name === 'find_people') return ok({ people: [{ ref: 'person:p1', name: 'Trần Văn Dũng' }, { ref: 'person:p2', name: 'Lê Ngọc Trâm' }], next_cursor: null });
    throw new Error(`Unexpected ${call.name} ${JSON.stringify(call.arguments)}`);
  }) }, tools } as unknown as McpConnection;
  await act(async () => { root!.render(createElement(Portal, { area: 'H', mcp, tools, sandboxUrl: new URL('https://sandbox.test'), onOpenLink: () => true, revealDpop: undefined })); });
}
const calls = (name?: string) => vi.mocked(mcp.client.callTool).mock.calls.map(c => c[0] as Call).filter(c => !name || c.name === name);
const tab = (name: string) => Array.from(container.querySelectorAll<HTMLButtonElement>('[role="tab"]')).find(t => t.textContent === name);
async function click(text: string, scope: ParentNode = container) { const b = Array.from(scope.querySelectorAll<HTMLButtonElement>('button')).find(x => x.textContent === text); expect(b, text).toBeTruthy(); await act(async () => b!.click()); }
async function type(label: string, value: string) {
  const el = container.querySelector<HTMLInputElement | HTMLSelectElement>(`[aria-label="${label}"]`)!; expect(el, label).toBeTruthy();
  await act(async () => { const proto = el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype; Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(el, value); el.dispatchEvent(new Event(el instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true })); });
}
const panel = () => container.querySelector<HTMLElement>('#h-panel-agents')!;
/** Bộ dữ liệu AgentHR thật theo hợp đồng; `where` của search_records được áp như máy chủ. */
function agentHr(extra?: (call: Call) => ToolResult | undefined) {
  return (call: Call) => {
    const custom = extra?.(call); if (custom) return custom;
    if (call.name !== 'search_records' && call.name !== 'get_record' && call.name !== 'get_history') return undefined;
    if (call.name === 'get_record') { const row = [agent1, agent2].find(a => a.ref === call.arguments.ref); return row ? ok({ record: { ...row, model_id: 'ho_so_agent', state: null } }) : undefined; }
    if (call.name === 'get_history') return ok({ subject: { ref: agent1.ref }, events: [{ at: '2026-10-21T09:12:00Z', actor: { ref: 'agent:chatgpt', name: 'ChatGPT', kind: 'agent' }, on_behalf_of: { ref: 'person:p1', name: 'Trần Văn Dũng' }, action: 'create_record', outcome: 'denied', summary: 'Phiếu vượt hạn mức giá trị', seq: 7 }], next_cursor: null });
    const where = (call.arguments.where || {}) as Data;
    const rows: Record<string, Data[]> = { ho_so_agent: [agent1, agent2], vai_tro_agent: [role], jd_agent: [jd1, jd2], su_co_agent: [incident], giay_phep_hanh_dong: [] };
    const match = (row: Data) => Object.entries(where).every(([k, v]) => { const f = { ...row, ...(row.values as Data) }[k]; return String(typeof f === 'object' && f ? (f as Data).ref : f).split(':').at(-1) === String(v); });
    return ok({ items: (rows[String(call.arguments.model_id)] || []).filter(match), next_cursor: null });
  };
}

it('hides "Nhân sự AI" entirely when the workspace catalog has no ho_so_agent (pack not installed)', async () => {
  await mount(() => undefined, models.filter(m => !['ho_so_agent', 'jd_agent'].includes(m.id)));
  expect(tab('Nhân viên')).toBeTruthy(); expect(tab('Cấp quyền')).toBeTruthy();
  expect(tab('Nhân sự AI')).toBeUndefined();
  expect(container.querySelector('#h-panel-agents')).toBeNull();
  expect(container.textContent).not.toContain('Tuyển agent');
  expect(calls('search_records')).toHaveLength(0);
});

it('lists agents from real rows: role, owner, today vs JD limit bar, status chip, and stats computed from rows only', async () => {
  await mount(agentHr());
  expect(calls('search_records')).toHaveLength(0); // chỉ tải khi mở tab
  await act(async () => tab('Nhân sự AI')!.click());
  expect(calls('search_records').map(c => c.arguments)).toEqual([
    { model_id: 'ho_so_agent', limit: 100 }, { model_id: 'vai_tro_agent', limit: 100 }, { model_id: 'jd_agent', limit: 200 }, { model_id: 'su_co_agent', limit: 200, where: { trang_thai: 'mo' } },
  ]);
  const rows = Array.from(panel().querySelectorAll('tbody tr'));
  expect(rows).toHaveLength(2);
  const cells = (i: number) => Array.from(rows[i].querySelectorAll('td')).map(td => td.textContent);
  expect(cells(0).slice(0, 5)).toEqual(['Trợ lý khochatgpt', 'Trợ lý kho', 'Trần Văn Dũng', '42/50', 'Đang làm']);
  expect(cells(1).slice(0, 5)).toEqual(['Trợ lý CSKHclaude', 'Chăm sóc khách hàng', 'Lê Ngọc Trâm', '30/30', 'Tạm dừng · hạn mức']);
  const bars = panel().querySelectorAll<HTMLElement>('[role="meter"]');
  expect(bars[0].className).toBe('bar warn'); expect(bars[0].querySelector('i')!.style.width).toBe('84%'); expect(bars[0].getAttribute('aria-valuenow')).toBe('42');
  expect(bars[1].className).toBe('bar full');
  expect(Array.from(panel().querySelectorAll('.stat')).map(s => s.textContent)).toEqual(['1Agent đang làm', '72Việc hôm nay', '1Đang tạm dừng', '1Sự cố mở']);
});

it('shows an honest empty state with no stats when there are no agents', async () => {
  await mount(call => call.name === 'search_records' ? ok({ items: [], next_cursor: null }) : undefined);
  await act(async () => tab('Nhân sự AI')!.click());
  expect(panel().textContent).toContain('Chưa có agent nào');
  expect(panel().querySelector('.stats')).toBeNull();
  expect(panel().querySelector('table')).toBeNull();
});

it('opens an agent profile: capabilities from the JD (json string), held submit without a release, limits, incidents, history', async () => {
  await mount(agentHr());
  await act(async () => tab('Nhân sự AI')!.click());
  await click('Mở Trợ lý kho', panel());
  expect(calls('get_record').at(-1)!.arguments).toEqual({ ref: 'ho_so_agent:a1' });
  expect(calls('search_records').slice(-3).map(c => c.arguments)).toEqual([
    { model_id: 'jd_agent', limit: 50, where: { ho_so: 'a1' } }, { model_id: 'giay_phep_hanh_dong', limit: 100, where: { ho_so: 'a1' } }, { model_id: 'su_co_agent', limit: 100, where: { ho_so: 'a1' } },
  ]);
  const region = (name: string) => panel().querySelector<HTMLElement>(`[aria-label="${name}"]`)!;
  expect(panel().querySelector('.agent-head')!.textContent).toContain('chatgpt, làm thay Trần Văn Dũng');
  const capRows = Array.from(region('Được làm').querySelectorAll('tbody tr')).map(r => Array.from(r.querySelectorAll('td')).map(td => td.textContent || td.querySelector('[aria-label]')?.getAttribute('aria-label')));
  expect(capRows).toEqual([['Tồn kho', 'Có', 'Không', 'Không', 'Không'], ['Phiếu nhập/xuất', 'Có', 'Có', 'Có', 'Giữ lại']]);
  expect(region('Hạn mức').textContent).toContain('42 / 50'); expect(region('Hạn mức').textContent).toContain('5.000.000 ₫'); expect(region('Hạn mức').textContent).toContain('08:00 – 18:00, T2–T6');
  expect(region('Giấy phép hành động').textContent).toContain('Chưa mở hành động nào. Agent chỉ soạn nháp.');
  expect(region('Sự cố').textContent).toContain('Vượt hạn mức giá trị'); expect(region('Sự cố').textContent).toContain('Mở');
  await click('Xem nhật ký', region('Nhật ký'));
  expect(calls('get_history').at(-1)!.arguments).toEqual({ ref: 'ho_so_agent:a1' });
  expect(region('Nhật ký').textContent).toContain('Phiếu vượt hạn mức giá trị'); expect(region('Nhật ký').textContent).toContain('thay Trần Văn Dũng'); expect(region('Nhật ký').textContent).toContain('Bị chặn');
  expect(Array.from(panel().querySelectorAll('.agent-head button')).map(b => b.textContent)).toEqual(['Đình chỉ', 'Cho nghỉ', 'Làm mới hồ sơ']);
});

it('suspends through the confirm dialog with update_record(tam_dung, thu_cong, reason) and the read revision', async () => {
  let suspended = false;
  await mount(agentHr(call => {
    if (call.name === 'update_record') { suspended = true; return ok({ record: { ...agent1, revision: 5, values: { ...agent1.values, trang_thai: 'tam_dung', ly_do_tam_dung: 'thu_cong' } } }); }
    if (call.name === 'get_record' && call.arguments.ref === 'jd_agent:j9') return ok({ record: { ref: 'jd_agent:j9', state: 'in_review', revision: 2, values: { canh_bao: [] } } });
    if (call.name === 'get_record' && suspended) return ok({ record: { ...agent1, revision: 5, values: { ...agent1.values, trang_thai: 'tam_dung', ly_do_tam_dung: 'thu_cong' } } });
    if (call.name === 'create_record') return ok({ record: { ref: 'jd_agent:j9', revision: 1, values: call.arguments.values } });
    if (call.name === 'submit_request') return ok({ request: { ref: 'jd_agent:j9', state: 'in_review', revision: 2 } });
    return undefined;
  }));
  await act(async () => tab('Nhân sự AI')!.click());
  await click('Mở Trợ lý kho', panel());
  await click('Đình chỉ', panel().querySelector('.agent-head')!);
  const dialog = container.querySelector<HTMLElement>('[role="dialog"]')!;
  expect(dialog.getAttribute('aria-modal')).toBe('true');
  expect(dialog.textContent).toContain('Đình chỉ Trợ lý kho?'); expect(dialog.textContent).toContain('Agent dừng ngay ở lượt kế tiếp');
  expect(calls('update_record')).toHaveLength(0);
  await click('Hủy', dialog); expect(container.querySelector('[role="dialog"]')).toBeNull(); expect(calls('update_record')).toHaveLength(0);
  await click('Đình chỉ', panel().querySelector('.agent-head')!);
  await type('Lý do', 'vượt hạn mức nhiều lần');
  await click('Xác nhận đình chỉ', container.querySelector('[role="dialog"]')!);
  expect(calls('update_record')).toEqual([{ name: 'update_record', arguments: { ref: 'ho_so_agent:a1', set: { trang_thai: 'tam_dung', ly_do_tam_dung: 'thu_cong' }, expected_revision: 4, reason: 'vượt hạn mức nhiều lần', idempotency_key: expect.any(String) } }]);
  expect(container.querySelector('[role="dialog"]')).toBeNull();
  expect(panel().querySelector('[data-testid="agenthr-done"]')!.textContent).toBe('Đã đình chỉ Trợ lý kho.');
  expect(panel().querySelector('.agent-head')!.textContent).toContain('Tạm dừng · thủ công');
  expect(Array.from(panel().querySelectorAll('.agent-head button')).map(b => b.textContent)).toEqual(['Mở lại', 'Cho nghỉ', 'Làm mới hồ sơ']);
  // Mở lại = yêu cầu đổi JD (không update_record dang_lam): tạo jd_agent từ JD hiệu lực rồi gửi duyệt
  await click('Mở lại', panel().querySelector('.agent-head')!);
  expect(container.querySelector<HTMLInputElement>('[aria-label="Việc mỗi ngày"]')!.value).toBe('50');
  expect(container.querySelector<HTMLSelectElement>('[aria-label="Mức tự chủ"]')!.value).toBe('1');
  await type('Việc mỗi ngày', '60');
  // (create_record / submit_request / get_record jd_agent:j9 do handler ở trên trả lời)
  await act(async () => container.querySelector('form[aria-label="Yêu cầu đổi mô tả công việc"]')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
  expect(calls('create_record').at(-1)!.arguments).toEqual({ model_id: 'jd_agent', values: { ho_so: 'a1', han_muc_viec_ngay: 60 }, idempotency_key: expect.any(String) });
  expect(calls('submit_request').at(-1)!.arguments).toEqual({ ref: 'jd_agent:j9', expected_revision: 1, idempotency_key: expect.any(String) });
  expect(calls('update_record')).toHaveLength(1);
  expect(panel().textContent).toContain('Không có cảnh báo quyền');
});

it('hires from a role card: create ho_so_agent, create jd_agent, submit_request in order; retry never recreates the profile; shows canh_bao', async () => {
  let jdAttempts = 0;
  await mount(agentHr(call => {
    if (call.name === 'create_record' && call.arguments.model_id === 'ho_so_agent') return ok({ record: { ref: 'ho_so_agent:new1', revision: 1, values: call.arguments.values } });
    if (call.name === 'create_record' && call.arguments.model_id === 'jd_agent') return ++jdAttempts === 1 ? fail('internal_error', 'Mất kết nối') : ok({ record: { ref: 'jd_agent:new1', revision: 1, values: call.arguments.values } });
    if (call.name === 'submit_request') return ok({ request: { ref: 'jd_agent:new1', state: 'in_review', revision: 2 }, route: [] });
    if (call.name === 'get_record' && call.arguments.ref === 'jd_agent:new1') return ok({ record: { ref: 'jd_agent:new1', state: 'in_review', revision: 2, values: { canh_bao: ['money_reach_expansion', { code: 'outbound_release', chi_tiet: 'Được tự gửi phiếu đi duyệt.' }, 'future_flag'], nang_luc: caps } } });
    return undefined;
  }));
  await act(async () => tab('Nhân sự AI')!.click());
  await click('Tuyển agent', panel());
  const card = Array.from(panel().querySelectorAll<HTMLButtonElement>('.role'));
  expect(card).toHaveLength(1);
  expect(card[0].textContent).toContain('Tra tồn, soạn phiếu nhập xuất.');
  expect(Array.from(card[0].querySelectorAll('.cap')).map(c => [c.textContent, c.classList.contains('w')])).toEqual([['Đọc Tồn kho', false], ['Đọc Phiếu nhập/xuất', false], ['Tạo Phiếu nhập/xuất', true], ['Sửa Phiếu nhập/xuất', true], ['Gửi duyệt Phiếu nhập/xuất', true]]);
  await act(async () => card[0].click());
  expect(card[0].getAttribute('aria-pressed')).toBe('true');
  expect(container.querySelector<HTMLInputElement>('[aria-label="Tên agent"]')!.value).toBe('Trợ lý kho');
  expect(container.querySelector<HTMLInputElement>('[aria-label="Việc mỗi ngày"]')!.value).toBe('50');
  expect(container.querySelector<HTMLSelectElement>('[aria-label="Làm thay"]')!.value).toBe('p1');
  await type('Mã client agent', 'chatgpt'); await type('Mức tự chủ', '2'); await type('Giá trị mỗi phiếu', '3000000');
  const submit = () => act(async () => container.querySelector('form[aria-label="Hồ sơ agent"]')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
  await submit();
  expect(panel().querySelector('[role="alert"]')!.textContent).toContain('Mất kết nối');
  expect(panel().textContent).toContain('Đã tạo hồ sơ agent');
  await submit();
  const creates = calls('create_record');
  expect(creates.map(c => c.arguments.model_id)).toEqual(['ho_so_agent', 'jd_agent', 'jd_agent']);
  expect(creates[0].arguments).toEqual({ model_id: 'ho_so_agent', values: { ten: 'Trợ lý kho', client_id: 'chatgpt', nguoi_phu_trach: 'p1', vai_tro: 'r1', trang_thai: 'cho_duyet' }, idempotency_key: expect.any(String) });
  expect(creates[1].arguments).toEqual({ model_id: 'jd_agent', values: { ho_so: 'new1', muc_tu_chu: 2, han_muc_gia_tri: 3000000 }, idempotency_key: expect.any(String) });
  expect(creates[2]).toEqual(creates[1]); // thử lại đúng thao tác, cùng khóa an toàn
  expect(creates[1].arguments.values).not.toHaveProperty('nang_luc'); // lõi sao năng lực + trường chưa đổi từ vai trò
  expect(calls('submit_request')).toEqual([{ name: 'submit_request', arguments: { ref: 'jd_agent:new1', expected_revision: 1, idempotency_key: expect.any(String) } }]);
  const all = calls(); expect(all.indexOf(creates[2])).toBeLessThan(all.indexOf(calls('submit_request')[0]));
  const flags = Array.from(panel().querySelectorAll('.flag')).map(f => f.textContent);
  expect(flags).toEqual(['Đụng tiềnThêm quyền ghi dữ liệu có giá trị tiền (phiếu, đề xuất chi).', 'Gửi đi duyệt / ra ngoàiĐược tự gửi phiếu đi duyệt.', 'future_flag']);
  expect(panel().querySelector('[data-testid="agenthr-done"]')!.textContent).toBe('Đã gửi IT duyệt Trợ lý kho.');
});

it('shows the jd_agent risk flags in Quy trình above Duyệt / Từ chối', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div'); document.body.append(container); root = createRoot(container);
  const tools = ['inspect_workspace', 'get_inbox', 'get_record', 'decide_request'].map(name => ({ name, inputSchema: { type: 'object' } })) as McpTool[];
  const jdRequest = { ref: 'jd_agent:j5', title: 'Tuyển agent · Trợ lý mua hàng', model_id: 'jd_agent', state: 'in_review', revision: 2, values: { ho_so: 'a5', canh_bao: JSON.stringify(['money_reach_expansion', 'outbound_release']), nang_luc: [{ model: 'stock_request', thao_tac: ['read', 'create', 'submit'] }] } };
  mcp = { client: { callTool: vi.fn(async (call: Call) => call.name === 'inspect_workspace' ? ok({ me: { name: 'IT' }, models, processes: [] })
    : call.name === 'get_inbox' ? ok({ counts: { approval: 1 }, items: [{ ref: jdRequest.ref, kind: 'approval', title: jdRequest.title, state: 'in_review' }], next_cursor: null })
      : ok({ record: jdRequest })) }, tools } as unknown as McpConnection;
  await act(async () => { root!.render(createElement(Portal, { area: 'P', mcp, tools, sandboxUrl: new URL('https://sandbox.test'), onOpenLink: () => true, revealDpop: undefined })); });
  await click(`Mở ${jdRequest.title}`);
  const risk = container.querySelector<HTMLElement>('[aria-label="Cảnh báo quyền"]')!;
  expect(Array.from(risk.querySelectorAll('.flag b')).map(b => b.textContent)).toEqual(['Đụng tiền', 'Gửi đi duyệt / ra ngoài']);
  expect(Array.from(risk.querySelectorAll('tbody td')).map(td => td.textContent || td.querySelector('[aria-label]')?.getAttribute('aria-label'))).toEqual(['Phiếu nhập/xuất', 'Có', 'Có', 'Không', 'Có']);
  const decide = container.querySelector('.decide-actions')!;
  expect(risk.compareDocumentPosition(decide) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(Array.from(decide.querySelectorAll('button')).map(b => b.textContent)).toEqual(['Duyệt', 'Từ chối']);
});

it('pure helpers read contract fields without inventing values', () => {
  expect(riskFlags('not json')).toEqual([]);
  expect(riskFlags([{ loai: 'autonomy_raise' }])).toEqual([{ code: 'autonomy_raise', title: 'Tăng mức tự chủ', detail: 'Mức tự chủ của agent cao hơn trước.' }]);
  expect(workload({ values: { so_viec_hom_nay: 5 } })).toEqual({ done: 5, limit: null, pct: 0, tone: '' });
  expect(workload({ values: { so_viec_hom_nay: 60 } }, { values: { han_muc_viec_ngay: 50 } }).pct).toBe(100);
  expect(capChips('[{"model":"x","thao_tac":["read"]}]', id => id.toUpperCase())).toEqual([{ label: 'Đọc X', write: false }]);
  expect(capChips([{ model: 'x', read: true, create: true, update: false, submit: false }], id => id.toUpperCase())).toEqual([{ label: 'Đọc X', write: false }, { label: 'Tạo X', write: true }]);
  const rows = [{ ref: 'jd_agent:1', state: 'approved', updated_at: '2026-10-01', values: { ho_so: 'h' } }, { ref: 'jd_agent:2', state: 'approved', updated_at: '2026-10-05', values: { ho_so: 'ho_so_agent:h' } }, { ref: 'jd_agent:3', state: 'in_review', values: { ho_so: 'h' } }];
  expect(jdFor(rows, 'h').active?.ref).toBe('jd_agent:2'); expect(jdFor(rows, 'h').pending?.ref).toBe('jd_agent:3'); expect(jdFor(rows, 'x')).toEqual({ active: undefined, pending: undefined });
});

it('reads capabilities in the flat core shape and the legacy thao_tac shape (list or "read,create" string)', () => {
  expect(capabilities([{ model: 'a', read: true, create: false, update: true, submit: false, ghi_chu: 'Cần giấy phép' }])).toEqual([{ model: 'a', ops: ['read', 'update'], note: 'Cần giấy phép' }]);
  expect(capabilities(JSON.stringify(legacyCaps))).toEqual([{ model: 'stock_balance', ops: ['read'] }, { model: 'stock_request', ops: ['read', 'create'] }]);
  expect(capabilities([{ model: 'a' }, { model: '' }, 'x'])).toEqual([{ model: 'a', ops: [] }]);
  // gửi lõi: luôn dạng phẳng, kể cả khi nguồn là dạng cũ
  expect(flatCapabilities(legacyCaps)).toEqual([{ model: 'stock_balance', read: true, create: false, update: false, submit: false }, { model: 'stock_request', read: true, create: true, update: false, submit: false }]);
});

it('formats money as integer VND and numeric strings from the core', () => {
  expect(money('5000000')).toBe('5.000.000 ₫'); expect(money(1250000)).toBe('1.250.000 ₫'); expect(money('0')).toBe('Không giới hạn'); expect(money(undefined)).toBe('—');
  expect(workload({ values: { so_viec_hom_nay: '42' } }, { values: { han_muc_viec_ngay: '50' } })).toMatchObject({ done: 42, limit: 50, pct: 84, tone: 'warn' });
  expect(dateVi('2026-10-23')).toBe('23/10/2026');
});

it('labels release permits and checks their validity window', () => {
  const label = (id: string) => ({ stock_request: 'Phiếu nhập/xuất' })[id] || id;
  expect(permitLabel('request.submit:stock_request', label)).toBe('Gửi duyệt: Phiếu nhập/xuất');
  expect(permitLabel('request.submit:*', label)).toBe('Gửi duyệt: mọi loại');
  expect(permitCovers({ hanh_dong: 'request.submit:*' }, 'x', '2026-10-23')).toBe(true);
  expect(permitCovers({ hanh_dong: 'request.submit:stock_request', den_ngay: '2026-10-22' }, 'stock_request', '2026-10-23')).toBe(false);
  expect(permitCovers({ hanh_dong: 'request.submit:stock_request', tu_ngay: '2026-11-01' }, 'stock_request', '2026-10-23')).toBe(false);
  expect(permitCovers({ hanh_dong: 'request.submit:other' }, 'stock_request', '2026-10-23')).toBe(false);
});

it('maps backend reasons (details.reason or code) to short Vietnamese messages and keeps unknown ones', () => {
  for (const reason of ['hire_requires_jd_approval', 'immutable_field', 'reopen_requires_jd_approval', 'portal_client', 'agent_terminated', 'behalf_permission']) {
    const text = agentErrorText(new ToolError('raw english', 'invalid_request', '', { reason }));
    expect(text, reason).not.toBe('raw english'); expect(text).toMatch(/[ạảãàáâấầẩẫậăắằẳẵặẹẻẽèéêếềểễệỉĩìíịọỏõòóôốồổỗộơớờởỡợụủũùúưứừửữựỳýỷỹỵđ]/i);
  }
  expect(agentErrorText(new ToolError('conflict', 'revision_conflict', '', {}))).toContain('Làm mới hồ sơ');
  expect(agentErrorText(new ToolError('Thiếu trường (mã yêu cầu: r-1)', 'invalid_request', 'r-1', { reason: 'portal_client' }))).toMatch(/Desk.*\(mã yêu cầu: r-1\)$/);
  expect(agentErrorText(new ToolError('Giữ nguyên', 'invalid_request', '', { reason: 'something_else' }))).toBe('Giữ nguyên');
  expect(agentErrorText(new Error('boom'))).toBe('boom');
});

it('prefers the jd_hieu_luc record as the active JD, falling back to the latest approved JD', () => {
  const rows = [{ ref: 'jd_agent:old', state: 'approved', updated_at: '2026-10-25', values: { ho_so: 'h' } }, { ref: 'jd_agent:cur', state: 'approved', updated_at: '2026-10-01', values: { ho_so: 'h' } }];
  expect(activeJd({ ref: 'ho_so_agent:h', values: { jd_hieu_luc: 'cur' } }, rows)?.ref).toBe('jd_agent:cur');
  expect(activeJd({ ref: 'ho_so_agent:h', values: {} }, rows)?.ref).toBe('jd_agent:old');
});

it('shows a hired agent waiting for IT as "Chờ IT duyệt" from trang_thai, with only "Cho nghỉ" available', async () => {
  const pendingAgent = { ref: 'ho_so_agent:n1', revision: 1, values: { ten: 'Trợ lý mới', client_id: 'claude', nguoi_phu_trach: 'p1', vai_tro: 'r1', trang_thai: 'cho_duyet', so_viec_hom_nay: '0' } };
  const waiting = { ref: 'jd_agent:w1', state: 'in_review', values: { ho_so: 'n1', nang_luc: caps } };
  await mount(call => {
    if (call.name === 'search_records') { const rows: Record<string, Data[]> = { ho_so_agent: [pendingAgent], vai_tro_agent: [role], jd_agent: [waiting], su_co_agent: [] }; return ok({ items: rows[String(call.arguments.model_id)] || [], next_cursor: null }); }
    if (call.name === 'get_record') return ok({ record: pendingAgent });
    return undefined;
  });
  await act(async () => tab('Nhân sự AI')!.click());
  expect(Array.from(panel().querySelectorAll('tbody tr')[0].querySelectorAll('td'))[4].textContent).toBe('Chờ IT duyệt');
  await click('Mở Trợ lý mới', panel());
  expect(panel().querySelector('.agent-head')!.textContent).toContain('Chờ IT duyệt');
  expect(panel().textContent).toContain('Có yêu cầu đổi mô tả công việc đang chờ IT duyệt.');
  expect(Array.from(panel().querySelectorAll('.agent-head button')).map(b => b.textContent)).toEqual(['Cho nghỉ', 'Làm mới hồ sơ']);
});

it('shows ngay_dem, a link to the active JD (jd_hieu_luc), and release permits as "Gửi duyệt: ..." with their end date', async () => {
  const permits = [{ ref: 'giay_phep_hanh_dong:g1', values: { ho_so: 'a1', hanh_dong: 'request.submit:stock_request', tu_ngay: '2026-10-01', den_ngay: '2099-12-31' } }, { ref: 'giay_phep_hanh_dong:g2', values: { ho_so: 'a1', hanh_dong: 'request.submit:*' } }];
  await mount(agentHr(call => call.name === 'search_records' && call.arguments.model_id === 'giay_phep_hanh_dong' ? ok({ items: permits, next_cursor: null }) : undefined));
  await act(async () => tab('Nhân sự AI')!.click());
  await click('Mở Trợ lý kho', panel());
  const region = (name: string) => panel().querySelector<HTMLElement>(`[aria-label="${name}"]`)!;
  expect(region('Hạn mức').textContent).toContain('Số việc tính cho ngày23/10/2026');
  expect(region('Giấy phép hành động').textContent).toContain('Gửi duyệt: Phiếu nhập/xuất'); expect(region('Giấy phép hành động').textContent).toContain('Từ 01/10/2026 Đến 31/12/2099');
  expect(region('Giấy phép hành động').textContent).toContain('Gửi duyệt: mọi loại');
  expect(Array.from(region('Được làm').querySelectorAll('tbody tr')).map(r => r.querySelectorAll('td')[4].textContent || r.querySelectorAll('td')[4].querySelector('[aria-label]')?.getAttribute('aria-label'))).toEqual(['Không', 'Có']);
  expect(panel().querySelector('form[aria-label="Mở giấy phép gửi duyệt"]')).toBeNull(); // đã được phủ hết
  expect(panel().querySelector('[aria-label="Cảnh báo quyền"]')).toBeNull();
  await click('Xem mô tả công việc', region('Hạn mức'));
  expect(panel().querySelector('[aria-label="Cảnh báo quyền"]')!.textContent).toContain('Phiếu nhập/xuất');
  await click('Ẩn mô tả công việc', region('Hạn mức'));
  expect(panel().querySelector('[aria-label="Cảnh báo quyền"]')).toBeNull();
});

it('opens a release permit with create_record(request.submit:<model>, den_ngay)', async () => {
  let created = false;
  await mount(agentHr(call => {
    if (call.name === 'create_record') { created = true; return ok({ record: { ref: 'giay_phep_hanh_dong:g9', revision: 1, values: call.arguments.values } }); }
    if (call.name === 'search_records' && call.arguments.model_id === 'giay_phep_hanh_dong' && created) return ok({ items: [{ ref: 'giay_phep_hanh_dong:g9', values: { ho_so: 'a1', hanh_dong: 'request.submit:stock_request', den_ngay: '2099-01-31' } }], next_cursor: null });
    return undefined;
  }));
  await act(async () => tab('Nhân sự AI')!.click());
  await click('Mở Trợ lý kho', panel());
  const select = panel().querySelector<HTMLSelectElement>('[aria-label="Loại phiếu được gửi duyệt"]')!;
  expect(Array.from(select.options).map(o => o.value)).toEqual(['', 'stock_request', '*']); // chỉ model có quyền submit, chưa được phủ
  await type('Loại phiếu được gửi duyệt', 'stock_request'); await type('Giấy phép đến ngày', '2099-01-31');
  await act(async () => panel().querySelector('form[aria-label="Mở giấy phép gửi duyệt"]')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
  expect(calls('create_record')).toEqual([{ name: 'create_record', arguments: { model_id: 'giay_phep_hanh_dong', values: { ho_so: 'a1', hanh_dong: 'request.submit:stock_request', den_ngay: '2099-01-31' }, idempotency_key: expect.any(String) } }]);
  expect(panel().querySelector('[data-testid="agenthr-done"]')!.textContent).toBe('Đã mở giấy phép gửi duyệt.');
  expect(panel().querySelector('[aria-label="Giấy phép hành động"]')!.textContent).toContain('Gửi duyệt: Phiếu nhập/xuất');
});

it('renders the legacy thao_tac capabilities of a JD (JSON string, "read,create")', async () => {
  await mount(agentHr());
  await act(async () => tab('Nhân sự AI')!.click());
  await click('Mở Trợ lý CSKH', panel());
  const capRows = Array.from(panel().querySelectorAll<HTMLElement>('[aria-label="Được làm"] tbody tr')).map(r => Array.from(r.querySelectorAll('td')).map(td => td.textContent || td.querySelector('[aria-label]')?.getAttribute('aria-label')));
  expect(capRows).toEqual([['Tồn kho', 'Có', 'Không', 'Không', 'Không'], ['Phiếu nhập/xuất', 'Có', 'Có', 'Không', 'Không']]);
});

it('retries Đình chỉ once after revision_conflict with the re-read revision; a second conflict shows the Vietnamese message', async () => {
  let attempts = 0;
  const conflict = () => fail('revision_conflict', 'Revision changed', { reason: 'revision_conflict', current_revision: 6 });
  await mount(agentHr(call => {
    if (call.name === 'update_record') { attempts++; return attempts === 1 ? conflict() : ok({ record: { ...agent1, revision: 7, values: { ...agent1.values, trang_thai: 'tam_dung', ly_do_tam_dung: 'thu_cong' } } }); }
    if (call.name === 'get_record' && attempts > 0) return ok({ record: { ...agent1, revision: 6 } });
    return undefined;
  }));
  await act(async () => tab('Nhân sự AI')!.click());
  await click('Mở Trợ lý kho', panel());
  await click('Đình chỉ', panel().querySelector('.agent-head')!);
  await click('Xác nhận đình chỉ', container.querySelector('[role="dialog"]')!);
  expect(calls('update_record').map(c => c.arguments.expected_revision)).toEqual([4, 6]);
  expect(panel().querySelector('[data-testid="agenthr-done"]')!.textContent).toBe('Đã đình chỉ Trợ lý kho.');
});

it('gives up after a second revision_conflict on Cho nghỉ and shows the short Vietnamese message', async () => {
  await mount(agentHr(call => {
    if (call.name === 'update_record') return fail('revision_conflict', 'Revision changed', { reason: 'revision_conflict' });
    return undefined;
  }));
  await act(async () => tab('Nhân sự AI')!.click());
  await click('Mở Trợ lý kho', panel());
  await click('Cho nghỉ', panel().querySelector('.agent-head')!);
  await click('Xác nhận cho nghỉ', container.querySelector('[role="dialog"]')!);
  expect(calls('update_record')).toHaveLength(2);
  expect(container.querySelector('[role="dialog"] [role="alert"]')!.textContent).toContain('Hồ sơ vừa được người khác cập nhật. Làm mới hồ sơ rồi thử lại.');
  expect(container.textContent).not.toContain('Revision changed');
});

it('shows the Vietnamese message for portal_client when hiring', async () => {
  await mount(agentHr(call => call.name === 'create_record' ? fail('invalid_request', 'Client cổng Desk ...', { reason: 'portal_client' }) : undefined));
  await act(async () => tab('Nhân sự AI')!.click());
  await click('Tuyển agent', panel());
  await act(async () => panel().querySelector<HTMLButtonElement>('.role')!.click());
  await type('Mã client agent', 'xt-desk-agent');
  await act(async () => container.querySelector('form[aria-label="Hồ sơ agent"]')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
  expect(panel().querySelector('[role="alert"]')!.textContent).toContain('Client của Desk là phiên của chính người dùng, không tuyển làm agent được.');
});

it('hiring with no changed limits sends only ho_so on jd_agent (no nang_luc, no limits)', async () => {
  await mount(agentHr(call => {
    if (call.name === 'create_record' && call.arguments.model_id === 'ho_so_agent') return ok({ record: { ref: 'ho_so_agent:new2', revision: 1, values: call.arguments.values } });
    if (call.name === 'create_record') return ok({ record: { ref: 'jd_agent:new2', revision: 1, values: call.arguments.values } });
    if (call.name === 'submit_request') return ok({ request: { ref: 'jd_agent:new2', state: 'in_review', revision: 2 } });
    return undefined;
  }));
  await act(async () => tab('Nhân sự AI')!.click());
  await click('Tuyển agent', panel());
  await act(async () => panel().querySelector<HTMLButtonElement>('.role')!.click());
  await type('Mã client agent', 'chatgpt');
  await act(async () => container.querySelector('form[aria-label="Hồ sơ agent"]')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
  expect(calls('create_record').map(c => c.arguments.values)).toEqual([{ ten: 'Trợ lý kho', client_id: 'chatgpt', nguoi_phu_trach: 'p1', vai_tro: 'r1', trang_thai: 'cho_duyet' }, { ho_so: 'new2' }]);
});
