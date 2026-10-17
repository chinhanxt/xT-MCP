// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { Component, type ReactNode } from 'react';
import { PortalAlert } from './PortalAlert';
import type { ErrorKind } from './portal-state';

type State = { failed: boolean; message: string };

/** Một không gian lỗi không làm mất khung điều hướng: chỉ phần nội dung được thay bằng thông báo. */
export class PortalBoundary extends Component<{ children?: ReactNode; label: string }, State> {
  state: State = { failed: false, message: '' };
  static getDerivedStateFromError(error: unknown): State {
    return { failed: true, message: error instanceof Error ? error.message : String(error) };
  }
  render() {
    if (!this.state.failed) return this.props.children;
    return <section className="portal" aria-label={this.props.label}><h2 tabIndex={-1}>{this.props.label}</h2>
      <div role="alert" className="note portal-error" data-state="crashed">
        <strong>Không gian {this.props.label} gặp lỗi.</strong> Các không gian khác vẫn dùng được.
        <p>{this.state.message}</p>
        <button onClick={() => this.setState({ failed: false, message: '' })}>Thử lại</button>
      </div>
    </section>;
  }
}

/** Hiển thị khi chưa có kết nối MCP: đang kết nối hoặc kết nối lỗi (không để trống). */
export function PortalPending({ label, error, kind = 'error' }: { label: string; error: string; kind?: ErrorKind }) {
  return <section className="portal" aria-label={label}><h2 tabIndex={-1}>{label}</h2>
    {error ? <PortalAlert text={error} kind={kind} /> : <p role="status">Đang kết nối xT…</p>}
  </section>;
}
