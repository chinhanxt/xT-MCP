// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { HttpError, object, choice } from './validation.ts';

export const weekdayNames = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'] as const;
export type Weekday = (typeof weekdayNames)[number];
export interface ScheduleConfig {
  frequency: 'hourly' | 'daily' | 'weekly' | 'monthly';
  time: string;
  weekdays?: Weekday[];
  monthly_days?: number[];
  on_minute?: number;
  timezone: string;
}
export const defaultTimezone = 'Asia/Ho_Chi_Minh';

export function scheduleInput(raw: unknown): ScheduleConfig {
  const v = object(raw);
  const frequency = choice(v.frequency, ['hourly', 'daily', 'weekly', 'monthly'] as const, 'Tần suất');
  const timezone = v.timezone === undefined ? defaultTimezone : String(v.timezone);
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: timezone });
  } catch {
    throw new HttpError(400, 'Múi giờ không hợp lệ.');
  }
  const config: ScheduleConfig = { frequency, time: '00:00', timezone };
  if (frequency === 'hourly') {
    const minute = v.on_minute === undefined ? 0 : v.on_minute;
    if (typeof minute !== 'number' || !Number.isInteger(minute) || minute < 0 || minute > 59)
      throw new HttpError(400, 'Phút phải từ 0 đến 59.');
    config.on_minute = minute;
    return config;
  }
  if (typeof v.time !== 'string' || !/^([01]\d|2[0-3]):[0-5]\d$/.test(v.time)) throw new HttpError(400, 'Giờ phải có dạng HH:MM.');
  config.time = v.time;
  if (frequency === 'weekly') {
    if (!Array.isArray(v.weekdays) || !v.weekdays.length || v.weekdays.some((d) => !weekdayNames.includes(d)))
      throw new HttpError(400, 'Chọn ít nhất một ngày trong tuần (mon..sun).');
    config.weekdays = [...new Set(v.weekdays as Weekday[])];
  }
  if (frequency === 'monthly') {
    if (
      !Array.isArray(v.monthly_days) ||
      !v.monthly_days.length ||
      v.monthly_days.length > 31 ||
      v.monthly_days.some((d) => typeof d !== 'number' || !Number.isInteger(d) || d < 1 || d > 31)
    )
      throw new HttpError(400, 'Chọn ít nhất một ngày trong tháng (1-31).');
    config.monthly_days = [...new Set(v.monthly_days as number[])].sort((a, b) => a - b);
  }
  return config;
}

function wall(ms: number, timeZone: string) {
  const out: Record<string, number> = {};
  for (const p of new Intl.DateTimeFormat('en-US', {
    timeZone, hourCycle: 'h23', year: 'numeric', month: 'numeric', day: 'numeric', hour: 'numeric', minute: 'numeric', second: 'numeric',
  }).formatToParts(new Date(ms)))
    if (p.type !== 'literal') out[p.type] = Number(p.value);
  return out;
}
const offsetAt = (ms: number, tz: string) => {
  const w = wall(ms, tz);
  return Date.UTC(w.year, w.month - 1, w.day, w.hour, w.minute, w.second) - Math.floor(ms / 1000) * 1000;
};
function wallToUtc(y: number, mo: number, d: number, h: number, mi: number, tz: string) {
  const guess = Date.UTC(y, mo - 1, d, h, mi);
  const first = guess - offsetAt(guess, tz);
  return guess - offsetAt(first, tz);
}

// Next fire instant strictly after `after` (epoch ms), in the schedule's timezone. Monthly days beyond the month's
// length fire on the last day of that month.
export function nextFire(config: ScheduleConfig, after: number): number | null {
  const start = wall(after, config.timezone);
  for (let i = -1; i < 800; i++) {
    const day = new Date(Date.UTC(start.year, start.month - 1, start.day + i));
    const y = day.getUTCFullYear(), mo = day.getUTCMonth() + 1, d = day.getUTCDate();
    if (config.frequency === 'weekly' && !config.weekdays!.includes(weekdayNames[day.getUTCDay()])) continue;
    if (config.frequency === 'monthly') {
      const dim = new Date(Date.UTC(y, mo, 0)).getUTCDate();
      if (!config.monthly_days!.some((n) => Math.min(n, dim) === d)) continue;
    }
    const times: [number, number][] =
      config.frequency === 'hourly'
        ? Array.from({ length: 24 }, (_, h) => [h, config.on_minute ?? 0] as [number, number])
        : [[Number(config.time.slice(0, 2)), Number(config.time.slice(3))]];
    for (const [h, mi] of times) {
      const at = wallToUtc(y, mo, d, h, mi, config.timezone);
      if (at > after) return at;
    }
  }
  return null;
}
