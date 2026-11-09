"""离线演练引擎：不调用模型，用 roles/<角色>/offline.toml 中的模板生成示例数据。

用于在本机没有 API Key 时验证整条编排链路（分派、传产物、门禁、回退、落盘）。
模板中的占位符：{{goal}} {{run_id}} {{attempt}} {{date}} {{feedback}} {{role_name}}
"""

from __future__ import annotations

import re
from typing import Any

from .config import RoleSpec
from .models import StageOutcome

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


def render(text: str, ctx: dict[str, Any]) -> str:
    def replace(match: re.Match) -> str:
        key = match.group(1)
        value = ctx.get(key)
        return str(value) if value is not None else match.group(0)

    return _PLACEHOLDER.sub(replace, text)


class OfflineEngine:
    def execute(
        self,
        role: RoleSpec,
        ctx: dict[str, Any],
        attempt: int,
        simulate_rework: bool = False,
    ) -> StageOutcome:
        data = role.offline or {}
        simulate_fail = simulate_rework and role.gate and attempt == 1

        if simulate_fail:
            verdict = "fail"
            summary = str(
                data.get("fail_summary")
                or f"{role.name} 演练判定不通过：存在阻断缺陷，需要上游返工。"
            )
            decisions = [str(x) for x in data.get("fail_decisions") or ["存在阻断缺陷，退回上游修复"]]
            raw_artifacts = data.get("fail_artifacts") or data.get("artifacts") or []
            notes = "交接条件未满足：阻断缺陷必须先修复。"
        else:
            configured = str(data.get("verdict") or "n/a").lower()
            verdict = configured if configured in {"pass", "fail"} else None
            summary = str(data.get("summary") or f"{role.name} 已完成离线演练。")
            decisions = [str(x) for x in data.get("decisions") or []]
            raw_artifacts = data.get("artifacts") or []
            handoff = data.get("handoff") or {}
            notes = str(handoff.get("notes") or "")

        artifacts: list[tuple[str, str]] = []
        for item in raw_artifacts:
            if isinstance(item, dict) and item.get("path") and item.get("content") is not None:
                artifacts.append((str(item["path"]), render(str(item["content"]), ctx)))

        if not artifacts:
            for name in role.outputs or ["output.md"]:
                body = (
                    f"# {role.name} · {name}\n\n"
                    f"- 目标：{ctx.get('goal', '')}\n"
                    f"- 运行：{ctx.get('run_id', '')}（第 {attempt} 次尝试）\n"
                    f"- 生成时间：{ctx.get('date', '')}\n\n"
                    f"> 离线演练占位产物：请在该角色的 offline.toml 中补充示例内容。\n"
                )
                artifacts.append((name, body))

        handoff_data = data.get("handoff") or {}
        handoff_ready = bool(handoff_data.get("ready", True)) if not simulate_fail else False

        return StageOutcome(
            summary=summary,
            decisions=decisions,
            verdict=verdict,
            artifacts=artifacts,
            handoff_ready=handoff_ready,
            handoff_notes=notes,
            risks=[str(x) for x in data.get("risks") or []],
            raw=(
                "# 离线演练输出（未调用模型）\n\n"
                f"- 角色：{role.name}\n- 判定：{verdict or 'n/a'}\n- 摘要：{summary}\n"
            ),
        )
