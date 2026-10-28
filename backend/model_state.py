"""河道模型状态快照：失败路径只在模型状态确实被清除时撤销报告。

底层工具在计算前会主动作废旧水质/水动力结果；如果随后因参数越界返回
error，路由不能只抛异常而留下“旧结果已没了、报告仍标记完成”的状态。
快照保留对象引用（而不是 id），避免旧对象释放后 id 复用造成误判。
"""

from __future__ import annotations


def snapshot(state) -> tuple:
    return (
        bool(getattr(state, 'model_initialized', False)),
        getattr(state, 'hydro_result', None),
        getattr(state, 'wq_result', None),
        getattr(state, 'geometry_revision', 0),
    )


def changed(before: tuple, state) -> bool:
    after = snapshot(state)
    return (before[0] != after[0]
            or before[1] is not after[1]
            or before[2] is not after[2]
            or before[3] != after[3])


def invalidate_if_changed(session, before: tuple, state) -> bool:
    """模型状态已变时同步撤销报告并推进结果版本；返回是否发生失效。"""
    if not changed(before, state):
        return False
    session.invalidate_report()
    session.results_revision += 1
    return True
