from security.workflow_guard import (
    next_state,
    can_approve,
    consume_token,
    version_ok,
    parallel_ok
)


def test_submit():
    assert next_state('draft', 'submit') == 'pending'

def test_skip():
    assert next_state('draft', 'approve') is None

def test_step():
    assert next_state('pending', 'approve') == 'approved'

def test_reject():
    assert next_state('pending', 'reject') == 'rejected'

def test_rejected_final():
    assert next_state('rejected', 'approve') is None

def test_role_ok():
    assert can_approve('finance', 'finance') is True

def test_role_bad():
    assert can_approve('sales', 'finance') is False

def test_token():
    assert consume_token('t1', set()) is True

def test_token_reuse():
    assert consume_token('t1', {'t1'}) is False

def test_version():
    assert version_ok(2, 2) is True

def test_version_old():
    assert version_ok(1, 2) is False

def test_parallel():
    assert parallel_ok(['a', 'b'], {'a'}) is False

def test_parallel_ok():
    assert parallel_ok(['a', 'b'], {'a', 'b'}) is True

def test_empty():
    assert next_state('', 'submit') is None

def test_delegate():
    assert can_approve('finance', 'finance', delegate='sales') is False
