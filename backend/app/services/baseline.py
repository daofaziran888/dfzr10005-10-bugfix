"""基线监控管理业务规则：状态流转、字段校验与筛选口径都收在这里。

基线关键路径不再使用静态快照：每次读取都按依赖图节点的当前状态实时重算，
节点动作成功后也会把处理回执写回基线配置清单，保证两个页面读到同一份结果。
"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "baseline"
DAG_MODULE = "dag"
REQUIRED_FIELDS = ["基线编号", "基线名称", "承诺时间"]
STATUS_ORDER = ["草稿", "监控中", "已延期", "已关闭"]
ACTION_RULES = {"启用监控": "草稿", "调整基线": "监控中", "关闭基线": "已延期"}
NEGATIVE_ACTIONS = []

DAG_STATUS_ORDER = ["草稿", "运行中", "已暂停", "已完成"]
TERMINAL_STATUS = DAG_STATUS_ORDER[-1]


def _split_codes(raw: Any) -> list[str]:
    """把「DAG-0001,DAG-0002」这类串拆成节点编号列表，空值与占位符返回空。"""
    text = str(raw or "").strip()
    if not text or text in {"—", "-", "无"}:
        return []
    return [part.strip() for part in text.replace("，", ",").split(",") if part.strip()]


def compute_critical_path(scope: list[str] | None = None) -> list[dict[str, Any]]:
    """按依赖图节点当前状态重算关键路径：已完成的节点移出，其余按上下游拓扑排序。"""
    rows = store.rows(DAG_MODULE)
    active: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = str(row.get("节点编号", "")).strip()
        if not code or row.get("status") == TERMINAL_STATUS:
            continue
        if scope and code not in scope:
            continue
        active[code] = row
    ordered: list[dict[str, Any]] = []
    remaining = dict(active)
    while remaining:
        ready = sorted(
            code
            for code, row in remaining.items()
            if not any(up in remaining for up in _split_codes(row.get("上游节点")))
        )
        if not ready:  # 存在环时按编号顺序兜底，保证关键路径可读而不是死循环
            ready = [sorted(remaining)[0]]
        for code in ready:
            ordered.append(remaining.pop(code))
    return ordered


def render_critical_path(scope: list[str] | None = None) -> str:
    """把关键路径渲染成「编号 名称 → 编号 名称」的展示串。"""
    ordered = compute_critical_path(scope)
    if not ordered:
        return "（关键路径已清空：节点均已完成）"
    return " → ".join(
        f"{row.get('节点编号', '')} {row.get('节点名称', '')}".strip() for row in ordered
    )


def refresh_baselines() -> None:
    """按依赖图节点最新状态回写每条基线的关键路径与监控状态（不沿用旧快照）。"""
    for row in store.rows(MODULE):
        scope = _split_codes(row.get("依赖范围"))
        path = compute_critical_path(scope or None)
        row["关键路径"] = render_critical_path(scope or None)
        row["监控状态"] = f"关键路径在途 {len(path)} 个节点" if path else "关键路径已清空"


def record_receipt(receipt: dict[str, Any]) -> None:
    """节点动作成功后：刷新全部基线关键路径，并把处理回执落进相关基线配置清单。"""
    refresh_baselines()
    node_code = str(receipt.get("节点编号", ""))
    for row in store.rows(MODULE):
        scope = _split_codes(row.get("依赖范围"))
        if scope and node_code not in scope:
            continue
        receipts = row.setdefault("处理回执", [])
        receipts.append(dict(receipt))
        row["最近产出"] = f"{receipt['回执编号']}：{receipt['动作']} → {receipt['变更后状态']}"


class BaselineService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        refresh_baselines()
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("基线编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        refresh_baselines()
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
            return None, f"基线配置 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于基线监控管理可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"基线配置已{action}"
