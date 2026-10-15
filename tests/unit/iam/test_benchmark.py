# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Benchmark 10k lượt evaluate (chạy bằng `pytest -m slow`)."""

from __future__ import annotations

import statistics
import time
from dataclasses import replace

import pytest

from tests.unit.iam.support import Host, actor, request, statement, with_resource
from tests.unit.iam.test_spike_cases import BASE, BOT, OTHER
from xt.domain.iam import PolicyDocument, PolicyEvaluator

N = 10_000


def _requests() -> list:
    return [
        request(),
        request(resource=OTHER),
        request(actor=actor(id="lea", roles=("lead",)), resource=OTHER),
        with_resource(
            request(actor=actor(id="mia", roles=("manager",)), resource=OTHER), department="hr"
        ),
        request(
            actor=actor(id="boss", roles=("approver",)), action="record.approve", resource=OTHER
        ),
        request(actor=replace(BOT, autonomy=0, behalf="alice"), action="record.read"),
    ]


def _doc(extra: int) -> PolicyDocument:
    filler = [
        statement(
            f"filler-{i}",
            subjects=[f"role:r{i}"],
            actions=[f"mod{i}.*"],
            resources=[f"xrn:acme:t{i}:*"],
        )
        for i in range(extra)
    ]
    return PolicyDocument.model_validate({**BASE, "statements": [*BASE["statements"], *filler]})


@pytest.mark.slow
@pytest.mark.parametrize("extra", [0, 200])
def test_10k_evaluations(extra: int) -> None:
    evaluator = PolicyEvaluator(_doc(extra))
    host = Host(evaluator, actors={"alice": actor(), "boss": actor(id="boss", roles=("approver",))})
    reqs = _requests()
    for name, fn in (("evaluator", evaluator.evaluate), ("guard", host.evaluate)):
        fn(reqs[0])
        times = []
        t0 = time.perf_counter()
        for i in range(N):
            s = time.perf_counter_ns()
            fn(reqs[i % len(reqs)])
            times.append(time.perf_counter_ns() - s)
        total = time.perf_counter() - t0
        times.sort()
        p50, p99 = statistics.median(times) / 1000, times[int(N * 0.99)] / 1000
        print(
            f"{name:9} stmts={6 + extra:4} {N / total:9.0f} eval/s p50={p50:.1f}us p99={p99:.1f}us"
        )
        assert total < 10, f"{name}: {N} lượt mất {total:.2f}s"
        assert p99 < 5_000, f"{name}: p99 {p99:.0f}us"
