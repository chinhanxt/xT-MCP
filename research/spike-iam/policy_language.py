# SPDX-License-Identifier: Apache-2.0
"""Closed JSON policy language shared by the three adapters, no expression eval."""
from copy import deepcopy
from functools import lru_cache
import math
import operator
import re

FIELDS = {'subject.id', 'subject.limit', 'subject.team', 'subject.department',
          'resource.owner', 'resource.creator', 'resource.amount', 'resource.team',
          'resource.department'}
OPS = {'eq': operator.eq, 'ne': operator.ne, 'le': operator.le}

@lru_cache(maxsize=4096)
def pattern(value):
    # Only '*' is special, full-string matching, case sensitive.
    return re.compile('.*'.join(re.escape(p) for p in value.split('*')), re.DOTALL)

def star(pat, value):
    return pattern(pat).fullmatch(value) is not None

def value(req, path):
    area, name = path.split('.')
    return getattr(req.actor, name) if area == 'subject' else req.resource[name]

def conditions(req, items):
    for c in items:
        try:
            lhs = value(req, c['left'])
            rhs = value(req, c['right']['ref']) if isinstance(c['right'], dict) else c['right']
            if lhs is None or rhs is None:
                return False
            if c['op'] == 'le':
                if type(lhs) not in (int, float) or type(rhs) not in (int, float):
                    return False
                if not math.isfinite(lhs) or not math.isfinite(rhs):
                    return False
            if not OPS[c['op']](lhs, rhs):
                return False
        except (KeyError, AttributeError, TypeError, ValueError):
            return False
    return True

def validate(doc):
    if set(doc) != {'version', 'tenant', 'statements'} or doc['version'] != '1':
        raise ValueError('unknown policy fields/version')
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', doc['tenant']):
        raise ValueError('invalid tenant')
    if not isinstance(doc['statements'], list) or len(doc['statements']) > 2000:
        raise ValueError('statement limit/type')
    ids = set()
    for s in doc['statements']:
        if set(s) != {'id', 'effect', 'subjects', 'actions', 'resources', 'conditions'}:
            raise ValueError('unknown statement fields')
        if s['effect'] not in ('Allow', 'Deny') or s['id'] in ids or not isinstance(s['id'], str) or not s['id']:
            raise ValueError('effect/id')
        ids.add(s['id'])
        for key in ('subjects', 'actions', 'resources'):
            if not isinstance(s[key], list) or not s[key] or len(s[key]) > 32 or not all(isinstance(x, str) and 0 < len(x) <= 256 and x.count('*') <= 8 for x in s[key]):
                raise ValueError('pattern limits')
        if not isinstance(s['conditions'], list) or len(s['conditions']) > 16:
            raise ValueError('condition limit/type')
        for c in s['conditions']:
            if set(c) != {'left', 'op', 'right'} or c['op'] not in OPS or c['left'] not in FIELDS:
                raise ValueError('unsupported condition')
            rhs = c['right']
            if isinstance(rhs, dict):
                if set(rhs) != {'ref'} or rhs['ref'] not in FIELDS:
                    raise ValueError('unsupported reference')
            elif type(rhs) not in (str, int, float, bool) or isinstance(rhs, float) and not math.isfinite(rhs):
                raise ValueError('unsupported literal')
    return deepcopy(doc)

