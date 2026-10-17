// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { useCallback, useEffect, useState } from 'react';

export const AREAS = ['H', 'P', 'D', 'I', 'Kho', 'Desk'] as const;
export type Area = (typeof AREAS)[number];

/** `#/H` -> `H`; mọi giá trị khác (kể cả lạ) -> Desk. Hash không chạm tới đường dẫn /auth/callback. */
export function areaFromHash(hash: string): Area {
  const name = hash.replace(/^#\/?/, '');
  return (AREAS as readonly string[]).includes(name) ? name as Area : 'Desk';
}
export const hashFor = (area: Area) => (area === 'Desk' ? '' : `#/${area}`);

/**
 * Không gian đang mở gắn với location.hash để nút Quay lại/Tiến của trình duyệt hoạt động.
 * `reset()` về Desk bằng replace (đăng xuất không để lại mục lịch sử trỏ vào dữ liệu cũ).
 */
export function useAreaRoute(): [Area, (area: Area) => void, () => void] {
  const [area, setArea] = useState<Area>(() => areaFromHash(location.hash));
  useEffect(() => {
    const sync = () => setArea(areaFromHash(location.hash));
    window.addEventListener('hashchange', sync);
    window.addEventListener('popstate', sync);
    return () => { window.removeEventListener('hashchange', sync); window.removeEventListener('popstate', sync); };
  }, []);
  const navigate = useCallback((next: Area) => {
    if (areaFromHash(location.hash) !== next) history.pushState(null, '', `${location.pathname}${location.search}${hashFor(next)}`);
    setArea(next);
  }, []);
  const reset = useCallback(() => {
    history.replaceState(null, '', `${location.pathname}${location.search}`);
    setArea('Desk');
  }, []);
  return [area, navigate, reset];
}
