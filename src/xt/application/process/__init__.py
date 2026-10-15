# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Application của process engine: runtime instance, poller timer start, use case và các port.

Không import SpiffWorkflow/lxml/psycopg: engine BPMN hiện thực `ports.ProcessEngine` ở
`xt.adapters.workflow.bpmn`, bảng timer hiện thực `ports.TimerFireStore` ở `xt.adapters.db`.
"""
