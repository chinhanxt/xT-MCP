// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Node data parsing, validation and pure evaluation for the multi-branch and data nodes of the workflow.
import type { CompareOperator, ConditionCase, ExtractorParameter, GraphNode } from './contracts.ts';
import { compareOperators, parameterTypes, xtToolNames } from './contracts.ts';
import { HttpError, choice, object, strings, text } from './validation.ts';

export const refPattern = /\{\{\s*([\w-]+)(?:\.([\w-]+))?\s*\}\}/g;
const handleId = /^[\w-]{1,20}$/;
const numericOperators: CompareOperator[] = ['eq', 'ne', 'gt', 'lt', 'ge', 'le'];

function list(value: unknown, field: string, max: number): unknown[] {
    if (!Array.isArray(value) || value.length > max) throw new HttpError(400, `${field} không hợp lệ (tối đa ${max}).`);
    return value;
}

// Shape-only parsing at the config boundary; graph-level rules live in validateNodeData.
export function nodeDataInput(d: Record<string, unknown>, data: GraphNode['data']) {
    for (const key of ['tool', 'args', 'query', 'instruction'] as const)
        if (d[key] !== undefined) data[key] = text(d[key], key, 20000, false);
    if (data.tool !== undefined && !xtToolNames.includes(data.tool)) throw new HttpError(400, `Công cụ xT "${data.tool}" không được cấp.`);
    if (d.cases !== undefined)
        data.cases = list(d.cases, 'Nhánh điều kiện', 10).map((raw) => {
            const c = object(raw);
            return {
                id: text(c.id, 'ID nhánh', 20),
                logical_operator: choice<'and' | 'or'>(c.logical_operator, ['and', 'or'], 'Toán tử logic'),
                conditions: list(c.conditions, 'Điều kiện con', 10).map((item) => {
                    const x = object(item);
                    return {
                        variable: text(x.variable, 'Biến điều kiện', 2000),
                        operator: choice(x.operator, compareOperators, 'Phép so sánh'),
                        value: x.value === undefined ? '' : text(x.value, 'Giá trị so sánh', 2000, false),
                    };
                }),
            };
        });
    if (d.classes !== undefined)
        data.classes = list(d.classes, 'Nhóm phân loại', 10).map((raw) => {
            const c = object(raw);
            return { id: text(c.id, 'ID nhóm', 20), name: text(c.name, 'Tên nhóm', 200) };
        });
    if (d.parameters !== undefined)
        data.parameters = list(d.parameters, 'Tham số', 20).map((raw) => {
            const p = object(raw);
            if (p.required !== undefined && typeof p.required !== 'boolean')
                throw new HttpError(400, 'Cờ bắt buộc của tham số không hợp lệ.');
            return {
                name: text(p.name, 'Tên tham số', 64),
                type: choice(p.type, parameterTypes, 'Kiểu tham số'),
                description: p.description === undefined ? '' : text(p.description, 'Mô tả tham số', 1000, false),
                required: p.required === true,
                ...(p.options === undefined ? {} : { options: strings(p.options, 'Lựa chọn tham số', 50) }),
            };
        });
    if (d.filter !== undefined) {
        const f = object(d.filter);
        data.filter = {
            ...(f.key === undefined ? {} : { key: text(f.key, 'Khóa lọc', 128, false) }),
            operator: choice(f.operator, compareOperators, 'Phép lọc'),
            value: f.value === undefined ? '' : text(f.value, 'Giá trị lọc', 2000, false),
        };
    }
    if (d.order !== undefined) {
        const o = object(d.order);
        data.order = {
            ...(o.key === undefined ? {} : { key: text(o.key, 'Khóa sắp xếp', 128, false) }),
            dir: choice<'asc' | 'desc'>(o.dir, ['asc', 'desc'], 'Chiều sắp xếp'),
        };
    }
    if (d.limit !== undefined) {
        if (typeof d.limit !== 'number' || !Number.isInteger(d.limit) || d.limit < 1 || d.limit > 1000)
            throw new HttpError(400, 'Giới hạn danh sách cần từ 1–1000.');
        data.limit = d.limit;
    }
    if (d.extract !== undefined) {
        if (
            d.extract !== 'first' &&
            d.extract !== 'last' &&
            (typeof d.extract !== 'number' || !Number.isInteger(d.extract) || d.extract < 1)
        )
            throw new HttpError(400, 'Lấy phần tử cần là first, last hoặc số thứ tự từ 1.');
        data.extract = d.extract;
    }
}

export const hasCases = (n: GraphNode) => n.type === 'condition' && n.data.cases !== undefined;

// Handle ids a branching node must expose; undefined for single-output nodes.
export function branchHandles(n: GraphNode): string[] | undefined {
    if (n.type === 'condition') return hasCases(n) ? [...n.data.cases!.map((c) => c.id), 'else'] : ['true', 'false'];
    if (n.type === 'question_classifier') return (n.data.classes ?? []).map((c) => c.id);
    return undefined;
}

// Every template-bearing string of a node, checked for {{refs}} by validateGraph.
export function nodeTemplates(n: GraphNode): string[] {
    const d = n.data;
    return [
        d.prompt,
        d.template,
        d.variable,
        d.url,
        d.body,
        d.fallback,
        d.args,
        d.query,
        d.instruction,
        d.filter?.value,
        ...(d.cases ?? []).flatMap((c) => c.conditions.flatMap((x) => [x.variable, x.value])),
    ].filter((value): value is string => !!value);
}

function uniqueIds(ids: string[], label: string, what: string) {
    if (!ids.length) throw new HttpError(400, `Node ${label} cần ít nhất một ${what}.`);
    if (new Set(ids).size !== ids.length) throw new HttpError(400, `Node ${label} có ${what} bị trùng ID.`);
    for (const id of ids) if (!handleId.test(id)) throw new HttpError(400, `ID ${what} "${id}" chỉ gồm chữ, số, _ hoặc - (tối đa 20).`);
}

export function validateNodeData(n: GraphNode) {
    const d = n.data,
        label = d.label;
    if (hasCases(n)) {
        uniqueIds(
            d.cases!.map((c) => c.id),
            label,
            'nhánh',
        );
        if (d.cases!.some((c) => c.id === 'else')) throw new HttpError(400, 'ID nhánh "else" được dành cho nhánh còn lại.');
        if (d.cases!.some((c) => !c.conditions.length)) throw new HttpError(400, `Mỗi nhánh của ${label} cần ít nhất một điều kiện.`);
    }
    if (n.type === 'xt_tool') {
        if (!xtToolNames.includes(d.tool ?? '')) throw new HttpError(400, `Công cụ xT "${d.tool ?? ''}" không được cấp.`);
        parseArgs(d.args ?? '{}', () => '');
    }
    if (n.type === 'question_classifier') {
        uniqueIds(
            (d.classes ?? []).map((c) => c.id),
            label,
            'nhóm',
        );
        if ((d.classes ?? []).some((c) => !c.name.trim())) throw new HttpError(400, `Nhóm của ${label} cần có tên.`);
    }
    if (n.type === 'parameter_extractor') {
        const params = d.parameters ?? [];
        if (!params.length) throw new HttpError(400, `Node ${label} cần ít nhất một tham số.`);
        if (new Set(params.map((p) => p.name)).size !== params.length) throw new HttpError(400, `Node ${label} có tham số bị trùng tên.`);
        for (const p of params) {
            if (!/^[A-Za-z_][\w-]{0,63}$/.test(p.name)) throw new HttpError(400, `Tên tham số "${p.name}" không hợp lệ.`);
            if (p.type === 'select' && !p.options?.length)
                throw new HttpError(400, `Tham số ${p.name} kiểu lựa chọn cần danh sách giá trị.`);
        }
    }
    if (n.type === 'list_operator' && !d.variable) throw new HttpError(400, `Node ${label} cần biến danh sách.`);
}

// Parses an args template; placeholders are substituted by `fill` before JSON.parse.
export function parseArgs(template: string, fill: (key: string, field?: string) => string): Record<string, unknown> {
    let parsed: unknown;
    try {
        parsed = JSON.parse(template.replace(refPattern, (_, key: string, field?: string) => fill(key, field)));
    } catch {
        throw new HttpError(400, 'Tham số công cụ xT phải là JSON hợp lệ; đặt {{biến}} bên trong chuỗi.');
    }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new HttpError(400, 'Tham số công cụ xT phải là JSON object.');
    return parsed as Record<string, unknown>;
}

export const jsonEscape = (value: string) => JSON.stringify(value).slice(1, -1);

// Reads `field` from the JSON object output of node `key`.
export function fieldValue(key: string, raw: string, field: string): string {
    let parsed: unknown;
    try {
        parsed = JSON.parse(raw);
    } catch {}
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
        throw new HttpError(400, `Kết quả ${key} không phải JSON object nên không đọc được ${key}.${field}.`);
    if (!Object.hasOwn(parsed, field)) throw new HttpError(400, `Kết quả ${key} không có trường ${field}.`);
    const value = (parsed as Record<string, unknown>)[field];
    return value === null || value === undefined ? '' : typeof value === 'string' ? value : JSON.stringify(value);
}

function number(value: string): number | undefined {
    if (!value.trim()) return undefined;
    const parsed = Number(value.trim());
    return Number.isFinite(parsed) ? parsed : undefined;
}

export function compare(operator: CompareOperator, left: string, right: string): boolean {
    if (numericOperators.includes(operator)) {
        const a = number(left),
            b = number(right);
        if (a === undefined || b === undefined) return false;
        return { eq: a === b, ne: a !== b, gt: a > b, lt: a < b, ge: a >= b, le: a <= b }[operator as 'eq'];
    }
    switch (operator) {
        case 'contains':
            return left.includes(right);
        case 'not_contains':
            return !left.includes(right);
        case 'starts_with':
            return left.startsWith(right);
        case 'ends_with':
            return left.endsWith(right);
        case 'is':
            return left === right;
        case 'is_not':
            return left !== right;
        case 'empty':
            return !left.trim();
        default:
            return !!left.trim();
    }
}

// First case whose conditions hold wins; otherwise 'else'.
export function matchCase(cases: ConditionCase[], render: (template: string) => string): string {
    for (const c of cases) {
        const results = c.conditions.map((x) => compare(x.operator, render(x.variable), render(x.value ?? '')));
        if (c.logical_operator === 'and' ? results.every(Boolean) : results.some(Boolean)) return c.id;
    }
    return 'else';
}

export function classifierPrompt(classes: { id: string; name: string }[], instruction: string) {
    return [
        'Phân loại câu hỏi của người dùng vào đúng một nhóm dưới đây.',
        ...classes.map((c) => `- id: ${c.id} | tên: ${c.name}`),
        instruction ? `Hướng dẫn thêm: ${instruction}` : '',
        'Chỉ trả về JSON {"class_id": "<id>"} và không viết gì thêm.',
    ]
        .filter(Boolean)
        .join('\n');
}

// Accepts JSON with an id/name field, a bare id or name, or text mentioning exactly one class.
export function parseClass<T extends { id: string; name: string }>(content: string, classes: T[]): T | undefined {
    const norm = (s: string) =>
        s
            .trim()
            .replace(/^["'`]+|["'`.]+$/g, '')
            .trim()
            .toLowerCase();
    const find = (value: unknown) =>
        typeof value === 'string' ? classes.find((c) => norm(c.id) === norm(value) || norm(c.name) === norm(value)) : undefined;
    const body = content
        .trim()
        .replace(/^```(?:json)?\s*|\s*```$/g, '')
        .trim();
    try {
        const parsed = JSON.parse(body);
        if (parsed && typeof parsed === 'object')
            for (const key of ['class_id', 'id', 'class', 'class_name', 'name', 'category']) {
                const hit = find(parsed[key]);
                if (hit) return hit;
            }
        const hit = find(parsed);
        if (hit) return hit;
    } catch {}
    const direct = find(body);
    if (direct) return direct;
    const lower = body.toLowerCase();
    const mentioned = classes.filter((c) => lower.includes(c.id.toLowerCase()) || lower.includes(c.name.toLowerCase()));
    return mentioned.length === 1 ? mentioned[0] : undefined;
}

export function extractorPrompt(parameters: ExtractorParameter[], instruction: string) {
    const spec = parameters.map((p) => ({
        name: p.name,
        type: p.type,
        description: p.description,
        required: p.required,
        ...(p.options ? { options: p.options } : {}),
    }));
    return [
        'Trích xuất tham số từ văn bản của người dùng.',
        `Danh sách tham số (JSON): ${JSON.stringify(spec)}`,
        instruction ? `Hướng dẫn thêm: ${instruction}` : '',
        'Trả về đúng một JSON object với các khóa là tên tham số; dùng null khi văn bản không có thông tin. Không thêm khóa khác.',
    ]
        .filter(Boolean)
        .join('\n');
}

function coerce(p: ExtractorParameter, value: unknown): unknown {
    const bad = () => new HttpError(502, `Tham số ${p.name} không đúng kiểu ${p.type}.`);
    if (p.type === 'number') {
        const parsed = typeof value === 'number' ? value : typeof value === 'string' ? number(value) : undefined;
        if (parsed === undefined || !Number.isFinite(parsed)) throw bad();
        return parsed;
    }
    if (p.type === 'boolean') {
        if (typeof value === 'boolean') return value;
        if (value === 'true' || value === 'false') return value === 'true';
        throw bad();
    }
    if (p.type === 'array[string]') {
        if (!Array.isArray(value) || value.some((x) => typeof x !== 'string')) throw bad();
        return value;
    }
    if (typeof value !== 'string') throw bad();
    if (p.type === 'select' && !p.options!.includes(value)) throw new HttpError(502, `Tham số ${p.name} không thuộc danh sách lựa chọn.`);
    return value;
}

export function extractParameters(content: string, parameters: ExtractorParameter[]): Record<string, unknown> {
    let parsed: unknown;
    try {
        parsed = JSON.parse(content);
    } catch {}
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
        throw new HttpError(502, 'Mô hình không trả về JSON object tham số.');
    const raw = parsed as Record<string, unknown>,
        result: Record<string, unknown> = {};
    for (const p of parameters) {
        const value = raw[p.name];
        if (value === undefined || value === null || value === '') {
            if (p.required) throw new HttpError(502, `Thiếu tham số bắt buộc ${p.name}.`);
            result[p.name] = null;
            continue;
        }
        result[p.name] = coerce(p, value);
    }
    return result;
}

const itemText = (value: unknown) =>
    value === undefined || value === null ? '' : typeof value === 'string' ? value : JSON.stringify(value);
const pick = (item: unknown, key?: string) =>
    key ? (item && typeof item === 'object' ? (item as Record<string, unknown>)[key] : undefined) : item;

// Filter, then order, then limit, then extract one item.
export function operateList(raw: string, data: GraphNode['data'], render: (template: string) => string): unknown {
    let items: unknown;
    try {
        items = JSON.parse(raw);
    } catch {}
    if (!Array.isArray(items)) throw new HttpError(400, 'Biến danh sách phải là mảng JSON.');
    let result = items;
    const filter = data.filter;
    if (filter) {
        const right = render(filter.value ?? '');
        result = result.filter((item) => compare(filter.operator, itemText(pick(item, filter.key)), right));
    }
    const order = data.order;
    if (order) {
        const sign = order.dir === 'desc' ? -1 : 1;
        result = [...result].sort((x, y) => {
            const a = pick(x, order.key),
                b = pick(y, order.key);
            if (typeof a === 'number' && typeof b === 'number') return (a - b) * sign;
            return itemText(a).localeCompare(itemText(b), 'vi') * sign;
        });
    }
    if (data.limit) result = result.slice(0, data.limit);
    if (data.extract === undefined) return result;
    const index = data.extract === 'first' ? 0 : data.extract === 'last' ? result.length - 1 : data.extract - 1;
    if (index < 0 || index >= result.length) throw new HttpError(400, `Danh sách không có phần tử ${data.extract} (còn ${result.length}).`);
    return result[index];
}
