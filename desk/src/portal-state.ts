// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { ToolError } from './portal-result';

export type ErrorKind = 'error' | 'forbidden' | 'expired';

/** Phân loại lỗi để mỗi không gian báo đúng: thiếu quyền, hết phiên hoặc lỗi chung. */
export function classifyError(error: unknown): ErrorKind {
  if (error instanceof ToolError) {
    if (error.code === 'access_denied') return 'forbidden';
    if (error.code === 'unauthenticated') return 'expired';
    return 'error';
  }
  const text = error instanceof Error ? error.message : String(error);
  if (/\b401\b|unauthori[sz]ed|invalid_token/i.test(text)) return 'expired';
  if (/\b403\b/.test(text)) return 'forbidden';
  return 'error';
}

export const stateTitle: Record<ErrorKind, string> = {
  error: 'Có lỗi',
  forbidden: 'Thiếu quyền',
  expired: 'Phiên đã hết hạn',
};

export const stateHint: Record<ErrorKind, string> = {
  error: '',
  forbidden: 'Tài khoản của bạn chưa được cấp quyền cho thao tác này. Liên hệ quản trị viên doanh nghiệp.',
  expired: 'Hãy đăng xuất rồi đăng nhập lại. Dữ liệu chưa được gửi lại.',
};
