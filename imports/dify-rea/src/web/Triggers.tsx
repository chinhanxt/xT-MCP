// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { useEffect, useState } from 'react';
import { api } from './api.ts';
import { ErrorBox } from './ui.tsx';

const days = [['mon', 'T2'], ['tue', 'T3'], ['wed', 'T4'], ['thu', 'T5'], ['fri', 'T6'], ['sat', 'T7'], ['sun', 'CN']] as const;
const freq: Record<string, string> = { hourly: 'Mỗi giờ', daily: 'Hằng ngày', weekly: 'Hằng tuần', monthly: 'Hằng tháng' };
function describe(c: any) {
  const when = c.frequency === 'hourly' ? `phút ${c.on_minute ?? 0}` : c.time;
  const extra = c.frequency === 'weekly' ? ' · ' + c.weekdays.join(', ') : c.frequency === 'monthly' ? ' · ngày ' + c.monthly_days.join(', ') : '';
  return `${freq[c.frequency]} · ${when}${extra} · ${c.timezone}`;
}

export function Triggers({ appId, published }: { appId: string; published: boolean }) {
  const [items, setItems] = useState<any[]>([]),
    [error, setError] = useState(''),
    [adding, setAdding] = useState(false),
    [frequency, setFrequency] = useState('daily'),
    [time, setTime] = useState('08:00'),
    [minute, setMinute] = useState(0),
    [weekdays, setWeekdays] = useState<string[]>(['mon']),
    [monthDays, setMonthDays] = useState('1'),
    [timezone, setTimezone] = useState('Asia/Ho_Chi_Minh');
  const base = `/apps/${encodeURIComponent(appId)}/triggers`;
  async function load() {
    try {
      setItems(await api(base));
    } catch (e) {
      setError((e as Error).message);
    }
  }
  useEffect(() => {
    load();
  }, [appId]);
  async function act(work: () => Promise<unknown>) {
    setError('');
    try {
      await work();
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  const create = () =>
    act(async () => {
      await api(base, 'POST', {
        kind: 'schedule',
        frequency,
        timezone,
        ...(frequency === 'hourly' ? { on_minute: minute } : { time }),
        ...(frequency === 'weekly' ? { weekdays } : {}),
        ...(frequency === 'monthly' ? { monthly_days: monthDays.split(/[ ,]+/).filter(Boolean).map(Number) } : {}),
      });
      setAdding(false);
    });
  return (
    <div className="panel" style={{ padding: 16 }}>
      <div className="page-heading">
        <div>
          <span className="eyebrow">KÍCH HOẠT</span>
          <h1>Kích hoạt theo lịch</h1>
          <p>{published ? 'Chỉ bản đã xuất bản được chạy tự động.' : 'Chưa xuất bản: lịch sẽ bị bỏ qua cho đến khi xuất bản.'}</p>
        </div>
        <button onClick={() => setAdding(!adding)}>{adding ? 'Đóng' : '+ Thêm lịch'}</button>
      </div>
      <ErrorBox error={error} />
      {adding && (
        <div className="panel" style={{ padding: 12, display: 'grid', gap: 8 }}>
          <label>Tần suất</label>
          <select value={frequency} onChange={(e) => setFrequency(e.target.value)}>
            {Object.entries(freq).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
          {frequency === 'hourly' ? (
            <>
              <label>Phút trong giờ</label>
              <input type="number" min={0} max={59} value={minute} onChange={(e) => setMinute(Number(e.target.value))} />
            </>
          ) : (
            <>
              <label>Giờ</label>
              <input type="time" value={time} onChange={(e) => setTime(e.target.value)} />
            </>
          )}
          {frequency === 'weekly' && (
            <div>
              {days.map(([k, label]) => (
                <label key={k} style={{ marginRight: 10 }}>
                  <input
                    type="checkbox"
                    checked={weekdays.includes(k)}
                    onChange={(e) => setWeekdays(e.target.checked ? [...weekdays, k] : weekdays.filter((d) => d !== k))}
                  />{' '}
                  {label}
                </label>
              ))}
            </div>
          )}
          {frequency === 'monthly' && (
            <>
              <label>Ngày trong tháng (cách nhau bằng dấu phẩy)</label>
              <input value={monthDays} onChange={(e) => setMonthDays(e.target.value)} />
            </>
          )}
          <label>Múi giờ</label>
          <input value={timezone} onChange={(e) => setTimezone(e.target.value)} />
          <button className="primary" onClick={create}>
            Tạo lịch
          </button>
        </div>
      )}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Lịch</th>
              <th>Lần tới</th>
              <th>Trạng thái</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {items.map((t) => (
              <tr key={t.id}>
                <td>{describe(t.config)}</td>
                <td>{t.enabled && t.next_fire_at ? new Date(t.next_fire_at).toLocaleString('vi-VN') : '—'}</td>
                <td>
                  <span className={'pill ' + (t.enabled ? 'green' : '')}>{t.enabled ? 'Đang bật' : 'Đã tắt'}</span>
                </td>
                <td>
                  <button className="text-btn" onClick={() => act(() => api(`${base}/${t.id}`, 'PATCH', { enabled: !t.enabled }))}>
                    {t.enabled ? 'Tắt' : 'Bật'}
                  </button>{' '}
                  <button
                    className="text-btn"
                    onClick={() => confirm('Xóa lịch này?') && act(() => api(`${base}/${t.id}`, 'DELETE'))}
                  >
                    Xóa
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!items.length && (
          <div className="empty">
            <h2>Chưa có lịch</h2>
          </div>
        )}
      </div>
    </div>
  );
}
