# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
import pathlib
import sys
import threading
import time

import pytest
import uvicorn

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from xt_evals.mock.server import build_app  # noqa: E402

PORT = 20181


@pytest.fixture(scope="session")
def mock_url():
    app, _ = build_app()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="error"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    yield f"http://127.0.0.1:{PORT}"
    server.should_exit = True
    th.join(timeout=5)
