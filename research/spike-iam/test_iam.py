# SPDX-License-Identifier: Apache-2.0
import pytest
from backends import BACKENDS
from cases import CASES, run_case

@pytest.mark.parametrize('engine', BACKENDS)
@pytest.mark.parametrize('case', CASES)
def test_iam(engine, case):
    run_case(case, BACKENDS[engine])
