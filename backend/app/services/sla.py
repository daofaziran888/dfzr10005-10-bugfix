"""SLA告警管理业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "sla"
REQUIRED_FIELDS = ["规则编号", "监控对象", "产出时间"]
STATUS_ORDER = ["草稿", "监控中", "已触发", "已关闭"]
ACTION_RULES = {"启用监控": "草稿", "处理告警": "监控中", "关闭规则": "已触发"}
NEGATIVE_ACTIONS = []


def record_risk_conclusion(receipt: dict[str, Any]) -> None:
    """旁路同步：节点动作的风险重算结论写入监控对象匹配的 SLA 规则。

    归档（撤销节点）后关联风险随之撤销，重新启动（恢复）后风险重新挂载，
    SLA 规则清单读取到的始终是最近一次重算结论。
    """
    node_code = str(receipt.get("节点编号", ""))
    conclusion = str(receipt.get("SLA风险重算结论", ""))
    for row in store.rows(MODULE):
        if node_code not in str(row.get("监控对象", "")):
            continue
        row["规则状态"] = conclusion
        row.setdefault("风险重算记录", []).append(
            {
                "回执编号": receipt.get("回执编号"),
                "节点编号": node_code,
                "结论": conclusion,
                "时间": receipt.get("时间"),
            }
        )


class SlaService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("规则编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"SLA规则 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于SLA告警管理可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"SLA规则已{action}"
