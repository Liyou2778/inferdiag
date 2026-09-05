"""规则执行器。

输入：时间窗聚合指标(dict)；输出：触发的 Finding 列表。
规则从 rules_v0.yaml 加载；任一条件涉及的指标缺失时跳过该规则。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

RULES_FILE = Path(__file__).with_name("rules_v0.yaml")

_OPS = {
    "ge": lambda a, b: a >= b,
    "gt": lambda a, b: a > b,
    "le": lambda a, b: a <= b,
    "lt": lambda a, b: a < b,
    "eq": lambda a, b: a == b,
}


def load_rules(path: str | Path | None = None) -> list[dict]:
    """加载规则；path 为空时用内置 rules_v0.yaml。"""
    target = Path(path) if path else RULES_FILE
    with target.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return list(data["rules"])


def evaluate(metrics: dict[str, Any], rules: list[dict] | None = None) -> list[dict]:
    """对聚合指标跑全部规则，返回触发的 finding 列表。

    finding: {
        rule_id, name, level, suggestion,
        evidence: {metric: 实际值, ...},  # 触发时涉及的各指标实际值
    }
    """
    rules = rules if rules is not None else load_rules()
    findings: list[dict] = []
    for rule in rules:
        conds = rule.get("conditions", [])
        ok = True
        evidence: dict[str, Any] = {}
        for cond in conds:
            metric = cond["metric"]
            value = metrics.get(metric)
            if value is None:
                ok = False  # 指标缺失：跳过该规则，避免误报
                break
            op = _OPS[cond["op"]]
            if not op(value, cond["value"]):
                ok = False
                break
            evidence[metric] = value
        if ok:
            findings.append(
                {
                    "rule_id": rule["id"],
                    "name": rule["name"],
                    "level": rule["level"],
                    "suggestion": rule["suggestion"],
                    "evidence": evidence,
                }
            )
    # 按严重度排序：critical -> warning -> info
    order = {"critical": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: order.get(f["level"], 9))
    return findings
