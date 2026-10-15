# SPDX-License-Identifier: Apache-2.0
"""10k-evaluation benchmark per engine: raw backend and through the shared Host PEP."""
from copy import deepcopy
from dataclasses import replace
import statistics
import sys
import time
from backends import BACKENDS
from contracts import Actor, Request
from cases import BASE, OTHER, BOT, host, statement

N = 10_000
REQS = [
    Request(),                                                         # own allow
    Request(resource=OTHER),                                           # implicit deny
    # Distinct ids per role set: the casbin adapter re-syncs g() when an id's roles change.
    Request(actor=Actor(id='lea', roles=('lead',)), resource=OTHER),   # team allow
    Request(actor=Actor(id='mia', roles=('manager',)), resource={**OTHER, 'department': 'hr'}),  # dept deny
    Request(actor=Actor(id='boss', roles=('approver',)), action='record.approve', resource=OTHER),  # ABAC allow
    Request(actor=replace(BOT, autonomy=0, behalf='alice'), action='record.read'),  # agent L0 read
]

def big(n):
    doc = deepcopy(BASE)
    doc['statements'] += [statement(f'filler-{i}', subjects=[f'role:r{i}'], actions=[f'mod{i}.*'],
                                    resources=[f'xrn:acme:t{i}:*']) for i in range(n)]
    return doc

def run(fn):
    times = []
    t0 = time.perf_counter()
    for i in range(N):
        s = time.perf_counter_ns()
        fn(REQS[i % len(REQS)])
        times.append(time.perf_counter_ns() - s)
    total = time.perf_counter() - t0
    times.sort()
    return total, statistics.median(times) / 1000, times[int(N * .99)] / 1000

if __name__ == '__main__':
    sizes = [int(x) for x in sys.argv[1:]] or [0, 200]
    print(f'N={N} evaluations, {len(REQS)} rotating requests')
    print(f"{'engine':7} {'stmts':>5} {'mode':7} {'build_ms':>8} {'total_s':>8} {'eval/s':>9} {'p50_us':>8} {'p99_us':>8}")
    for extra in sizes:
        doc = big(extra)
        for name, cls in BACKENDS.items():
            t = time.perf_counter(); h = host(cls, doc); build = (time.perf_counter() - t) * 1000
            for mode, fn in (('backend', h.backend.evaluate), ('host', h.evaluate)):
                fn(REQS[0])  # warm caches
                total, p50, p99 = run(fn)
                print(f'{name:7} {len(doc["statements"]):5} {mode:7} {build:8.1f} {total:8.3f} {N/total:9.0f} {p50:8.1f} {p99:8.1f}')
