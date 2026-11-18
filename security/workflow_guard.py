"""审批流服务端守卫（pairwise-workflow-guard）。

安全模型：目标状态永远由服务端根据白名单推导，客户端只提交动作，
不允许直接提交目标状态（如直接传 ``approved`` 跳级）。

覆盖的修复点：
1. 状态服务端推进 + 迁移白名单（``next_state``）；
2. 驳回为终态，终态上的任何动作都被拒绝（``next_state``）；
3. 节点 token 一次性，可选节点绑定以阻止跨节点重放（``consume_token``）；
4. 当前节点角色校验（``can_approve``）；
5. 委托审批必须落在显式范围内，无范围即拒绝（``can_approve``）；
6. 并行分支汇合校验，缺一支都不能整体通过（``parallel_ok``）；
7. 单据版本号校验，旧版本提交一律拒绝（``version_ok``）；
8. 批量审批逐条校验，任一条失败不牵连、也不跳过其它条（``batch_approve``）；
9. 审批历史只追加，写入的记录冻结、不可改写（``append_history``）。
"""

from types import MappingProxyType

# 状态迁移白名单：键为 (当前状态, 动作)，值为服务端推导出的下一状态。
# 多级审批扩展时只需在此表登记新的 (节点状态, 动作) 映射，
# 不在表中的组合一律返回 None，即拒绝迁移。
_TRANSITIONS = MappingProxyType({
    ('draft', 'submit'): 'pending',
    ('pending', 'approve'): 'approved',
    ('pending', 'reject'): 'rejected',
})

# 终态集合：处于终态的单据不接受任何进一步动作。
_TERMINAL_STATES = frozenset({'approved', 'rejected'})


def next_state(current, action):
    """服务端推进状态机。

    客户端只能给出动作（submit/approve/reject），下一状态由白名单推导；
    白名单未登记的迁移、空状态/空动作、驳回/通过等终态上的动作均返回
    ``None`` 表示拒绝，从而杜绝跳级通过与“驳回后继续 approve”。
    """
    if not isinstance(current, str) or not isinstance(action, str):
        return None
    if not current or not action:
        return None
    if current in _TERMINAL_STATES:
        return None
    return _TRANSITIONS.get((current, action))


def can_approve(required_role, actual_role, delegate=None, delegation_scope=None):
    """校验当前审批节点的角色权限。

    - 无委托时：登录身份角色必须与当前节点要求的角色完全一致，
      “只校验登录、不校验节点角色”会被拒绝；
    - 有委托时（``delegate`` 为受托人角色）：必须提供显式的
      ``delegation_scope``（节点角色 -> 可受托角色集合），且受托人
      落在该节点的受托范围内；没有范围或越出范围一律拒绝。
    """
    if not isinstance(required_role, str) or not required_role:
        return False
    if not isinstance(actual_role, str) or not actual_role:
        return False

    if delegate is None:
        return actual_role == required_role

    # 委托路径：实际操作者按受托人角色重新校验，且必须有显式范围。
    if not isinstance(delegate, str) or not delegate:
        return False
    if delegation_scope is None:
        return False
    allowed = delegation_scope.get(required_role)
    if not allowed:
        return False
    return delegate in allowed


def consume_token(token, consumed, node=None, issued_for=None):
    """消费节点审批 token，保证一次性。

    ``consumed`` 为服务端持久化的已用 token 集合：

    - token 已在集合中 -> 重放，拒绝（返回 ``False``）；
    - 可选 ``node`` + ``issued_for``：校验 token 与当前节点绑定，
      上一节点的 token 拿到下一节点使用同样拒绝；
    - 校验通过后将 token 记入集合并返回 ``True``。
    """
    if not isinstance(token, str) or not token:
        return False
    if not isinstance(consumed, set):
        return False

    if token in consumed:
        return False

    if node is not None and issued_for is not None:
        if issued_for.get(token) != node:
            return False

    consumed.add(token)
    return True


def version_ok(submitted, current):
    """校验单据版本号：提交版本必须与服务端当前版本完全一致。

    旧版本（``submitted < current``）以及任何非整数版本号均拒绝，
    防止基于过期快照重新提交覆盖新数据。
    """
    # bool 是 int 的子类，显式排除，避免 True/False 混入版本号。
    if isinstance(submitted, bool) or isinstance(current, bool):
        return False
    if not isinstance(submitted, int) or not isinstance(current, int):
        return False
    return submitted == current


def parallel_ok(required_branches, completed_branches):
    """并行分支汇合校验：所有必需分支都完成才允许整体通过。

    只完成其中一个分支（如 {'a'} 而要求 ['a', 'b']）返回 ``False``。
    """
    required = set(required_branches)
    if not required:
        return False
    return required.issubset(set(completed_branches))


def batch_approve(items, check):
    """批量审批逐条校验，不跳过任何一条。

    对 ``items`` 中的每条单据独立执行 ``check(item)``；单条抛异常或
    返回假值只判定该条失败，不会中断、也不会跳过其余单据的校验。
    返回与输入等长、同序的结果列表，由调用方决定整体提交还是部分驳回。
    """
    results = []
    for item in items:
        try:
            ok = bool(check(item))
        except Exception:
            ok = False
        results.append({'item': item, 'ok': ok})
    return results


def append_history(history, entry):
    """向审批历史只追加一条不可变记录。

    历史只暴露追加操作：每条记录在写入时冻结（``MappingProxyType``），
    之后无法修改/删除，审批意见不能改写既有历史。返回写入的冻结记录，
    其 ``seq`` 由服务端按当前长度生成，客户端无法指定。
    """
    if not isinstance(history, list):
        raise TypeError('history must be a list')
    if not isinstance(entry, dict) or not entry:
        raise ValueError('entry must be a non-empty mapping')

    record = MappingProxyType(dict(entry, seq=len(history) + 1))
    history.append(record)
    return record
