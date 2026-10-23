# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
# Chỉ môi trường demo (profile `demo`). Build context: deploy/demo-ops. Chỉ stdlib, không cài gói.
FROM python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f
# /state thuộc uid 10001 (cùng uid với xt-api) để volume mới tạo ghi được; xt-api chỉ mount read-only.
RUN useradd --system --uid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin demo \
    && mkdir /app /state && chown 10001:10001 /state
COPY server.py index.html /app/
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER 10001
EXPOSE 8090
ENTRYPOINT ["python", "/app/server.py"]
