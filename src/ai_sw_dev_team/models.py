"""角色结构化输出的解析与校验。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .config import RoleSpec


@dataclass
class StageOutcome:
    summary: str
    decisions: list[str] = field(default_factory=list)
    verdict: str | None = None
    artifacts: list[tuple[str, str]] = field(default_factory=list)
    handoff_ready: bool = True
    handoff_notes: str = ""
    risks: list[str] = field(default_factory=list)
    raw: str = ""
    warnings: list[str] = field(default_factory=list)


def _extract_json(text: str) -> dict | None:
    """从模型输出中提取第一个可解析的 JSON 对象（容忍 Markdown 包裹）。"""
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            obj, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict):
            return obj
        start = text.find("{", start + 1)
    return None


def _as_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def parse_structured(raw: str, role: RoleSpec) -> StageOutcome:
    payload = _extract_json(raw)
    if payload is None:
        fallback_name = role.outputs[0] if role.outputs else "output.md"
        return StageOutcome(
            summary=f"{role.name} 输出未按 JSON 协议返回，已按纯文本产物兜底。",
            artifacts=[(fallback_name, raw)],
            handoff_ready=True,
            handoff_notes="模型未返回结构化结论，请人工复核该阶段产物。",
            raw=raw,
            warnings=["模型输出不是合法 JSON，已降级为单文件产物"],
        )

    summary = str(payload.get("summary") or "").strip()
    verdict_raw = payload.get("verdict")
    verdict = str(verdict_raw).strip().lower() if verdict_raw not in (None, "") else None
    if verdict in {"pass", "通过", "ok", "true"}:
        verdict = "pass"
    elif verdict in {"fail", "不通过", "rejected", "false"}:
        verdict = "fail"
    elif verdict in {"n/a", "na", "none", "无"}:
        verdict = "n/a"

    handoff = payload.get("handoff") if isinstance(payload.get("handoff"), dict) else {}
    artifacts: list[tuple[str, str]] = []
    for item in payload.get("artifacts") or []:
        if isinstance(item, dict) and item.get("path") and item.get("content") is not None:
            artifacts.append((str(item["path"]).strip(), str(item["content"])))
    warnings: list[str] = []
    if not summary:
        summary = f"{role.name} 已完成"
        warnings.append("模型未返回 summary，已使用默认描述")
    if not artifacts:
        warnings.append("模型未返回 artifacts，本阶段没有产生交付物")
    if handoff and handoff.get("ready") is False:
        warnings.append("模型声明本阶段交接条件未满足")

    return StageOutcome(
        summary=summary,
        decisions=_as_list(payload.get("decisions")),
        verdict=verdict,
        artifacts=artifacts,
        handoff_ready=bool(handoff.get("ready", True)),
        handoff_notes=str(handoff.get("notes") or ""),
        risks=_as_list(payload.get("risks")),
        raw=raw,
        warnings=warnings,
    )
