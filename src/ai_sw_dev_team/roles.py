"""角色提示词组装：把配置化章程 + 输出协议 + 运行上下文拼成模型消息。

角色职责正文来自 roles/<角色>/prompt.md，代码中不保存任何角色长提示词。
"""

from __future__ import annotations

from .config import RoleSpec


def _bullets(items: list[str], empty: str = "（无）") -> str:
    if not items:
        return f"- {empty}"
    return "\n".join(f"- {item}" for item in items)


def render_contract(role: RoleSpec) -> str:
    """把 role.toml 的结构化契约渲染成提示词片段。"""
    lines = [f"## 角色契约：{role.name}（{role.id}）", "", "### 职责"]
    lines.append(_bullets(role.responsibilities))
    lines.extend(["", "### 输入"])
    lines.append(_bullets(role.inputs))
    lines.extend(["", "### 必须交付"])
    if role.deliverables:
        for item in role.deliverables:
            lines.append(f"- `{item.get('path', '')}`：{item.get('description', '')}")
    else:
        lines.append("- 见输出协议")

    handoff = role.handoff or {}
    lines.extend(["", "### 交接条件（满足后才能进入下一角色）"])
    ready_when = [str(x) for x in handoff.get("ready_when") or []]
    lines.append(_bullets(ready_when, empty="按输出协议判断"))
    if handoff.get("to"):
        lines.append(f"- 下游角色：{handoff['to']}")
    if handoff.get("notes"):
        lines.append(f"- 说明：{handoff['notes']}")

    lines.extend(["", "### 禁止越界"])
    lines.append(_bullets(role.guardrails, empty="不得越出本角色职责范围"))
    return "\n".join(lines)


def truncate(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + "\n\n...（内容超长，已截断）"


def build_messages(
    role: RoleSpec,
    goal: str,
    upstream: str,
    feedback: str | None,
    protocol: str,
    attempt: int,
    run_id: str,
    max_context_chars: int,
) -> tuple[str, str]:
    system = "\n\n---\n\n".join(
        part for part in (role.prompt.strip(), protocol.strip(), render_contract(role)) if part.strip()
    )
    user_parts = [
        f"# 团队目标\n{goal}",
        f"# 本次运行\n- run_id: {run_id}\n- 当前角色: {role.name}\n- 第 {attempt} 次尝试",
        "# 上游结论与产物\n" + (upstream.strip() or "（本角色是首个阶段，无上游产物）"),
    ]
    if feedback:
        user_parts.append("# 返工反馈（必须逐条处理）\n" + truncate(feedback, max_context_chars // 2))
    user_parts.append("# 最后提醒\n只输出一个 JSON 对象，字段与协议一致。")
    return system, "\n\n".join(user_parts)
