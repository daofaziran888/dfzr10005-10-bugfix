"""任务依赖编排业务规则：状态流转、字段校验与筛选口径都收在这里。

状态机只允许单向推进：草稿 → 运行中 → 已暂停 → 已完成，不允许回退或跳级。
暂停只作用于运行中节点，归档只能发生在暂停之后；非法动作不会改动节点与基线快照。
动作成功后会生成一条 SLA 风险重算结论（处理回执），同步落到基线配置清单与 SLA 规则。
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from app.services import baseline as baseline_sync
from app.services import sla as sla_sync
from app.store import store

MODULE = "dag"
REQUIRED_FIELDS = ["节点编号", "节点名称", "上游节点"]
STATUS_ORDER = ["草稿", "运行中", "已暂停", "已完成"]
# 动作 → (要求的前置状态, 推进到的目标状态)：只允许沿 STATUS_ORDER 向前走一格
ACTION_RULES = {
    "启动节点": ("草稿", "运行中"),
    "暂停节点": ("运行中", "已暂停"),
    "归档节点": ("已暂停", "已完成"),
}
NEGATIVE_ACTIONS = []

# 旧节点状态兼容：历史数据里出现过的状态值统一映射到当前状态机
LEGACY_STATUS_MAP = {"已归档": "已完成", "已取消": "已完成", "待启动": "草稿"}

# 两个入口（列表页、状态抽屉）争抢同一节点时串行化：后到请求在锁内读到已写入的终态
_ACTION_LOCK = threading.RLock()


def _normalize_rows() -> None:
    """旧数据兼容：把历史状态值映射进状态机，并让展示字段「节点状态」与真实状态一致。"""
    for row in store.rows(MODULE):
        status = str(row.get("status") or "").strip()
        if status not in STATUS_ORDER:
            status = LEGACY_STATUS_MAP.get(status, STATUS_ORDER[0])
            row["status"] = status
        if row.get("节点状态") != status:
            row["节点状态"] = status
        row["pending"] = status != STATUS_ORDER[-1]


def _risk_conclusion(action: str, row: dict[str, Any], path_count: int) -> str:
    code = row.get("节点编号", "")
    name = row.get("节点名称", "")
    if action == "启动节点":
        return f"SLA风险重算：节点{code}（{name}）已进入运行中，关键路径在途 {path_count} 个节点，按承诺时间重新挂载风险监控"
    if action == "暂停节点":
        return f"SLA风险重算：节点{code}（{name}）已暂停，关键路径在途 {path_count} 个节点，产出风险挂起，恢复后重新倒计时"
    return f"SLA风险重算：节点{code}（{name}）已完成归档并移出关键路径，关联SLA风险撤销"


class DagService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        _normalize_rows()
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("节点编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        _normalize_rows()
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        with _ACTION_LOCK:
            _normalize_rows()
            rows = store.rows(MODULE)
            entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
            entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
            for field in ("下游节点", "执行条件", "超时分钟", "失败策略"):
                if values.get(field) is not None:
                    entry[field] = values.get(field)
            entry["status"] = STATUS_ORDER[0]
            entry["节点状态"] = STATUS_ORDER[0]
            entry["pending"] = True
            entry["abnormal"] = False
            entry["处理回执"] = []
            rows.append(entry)
        baseline_sync.refresh_baselines()
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        with _ACTION_LOCK:
            _normalize_rows()
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, f"依赖图节点 {entry_id} 不存在或已归档"
            if action not in ACTION_RULES:
                return None, f"动作「{action}」不属于任务依赖编排可执行范围"
            receipts: list[dict[str, Any]] = entry.setdefault("处理回执", [])
            # 幂等：同一节点同一动作只生效一次，重复提交直接返回已写入的终态
            for receipt in receipts:
                if receipt.get("动作") == action:
                    return entry, f"动作「{action}」已生效过，重复提交不再处理，当前状态：{entry['status']}"
            required, target = ACTION_RULES[action]
            current = str(entry.get("status") or "")
            if current != required:
                return None, (
                    f"动作「{action}」要求节点处于「{required}」，当前为「{current}」，"
                    "状态只能按 草稿→运行中→已暂停→已完成 单向推进"
                )
            if STATUS_ORDER.index(target) != STATUS_ORDER.index(current) + 1:
                return None, f"目标状态「{target}」不允许从「{current}」跳转，禁止回退或跳级"
            entry["status"] = target
            entry["节点状态"] = target
            entry["pending"] = target != STATUS_ORDER[-1]
            entry["abnormal"] = action in NEGATIVE_ACTIONS
            # 先按新状态重算关键路径，再组装回执，保证结论读取的是最新基线
            baseline_sync.refresh_baselines()
            path = baseline_sync.compute_critical_path()
            receipt = {
                "回执编号": f"RCPT-{entry.get('节点编号', entry_id)}-{len(receipts) + 1:02d}",
                "节点编号": entry.get("节点编号"),
                "节点名称": entry.get("节点名称"),
                "动作": action,
                "变更前状态": current,
                "变更后状态": target,
                "关键路径": baseline_sync.render_critical_path(),
                "SLA风险重算结论": _risk_conclusion(action, entry, len(path)),
                "时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            # 成功推进生成一条 SLA 风险重算结论，并同步基线清单与 SLA 规则
            receipts.append(receipt)
            baseline_sync.record_receipt(receipt)
            sla_sync.record_risk_conclusion(receipt)
            return entry, f"依赖图节点已{action}，当前状态：{target}"
