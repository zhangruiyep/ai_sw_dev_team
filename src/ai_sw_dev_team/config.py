"""配置加载：团队流水线、角色契约、质量门禁、模型供应商。

配置一律使用 TOML（Python 3.11+ 标准库 tomllib），不引入第三方依赖。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
PLACEHOLDER_KEY_MARK = "替换为你的密钥"


class TeamError(RuntimeError):
    """可预期的配置/运行错误，CLI 会转成结构化失败结果而不是堆栈。"""


@dataclass
class RoleSpec:
    id: str
    name: str
    order: int
    gate: bool
    responsibilities: list[str]
    inputs: list[str]
    outputs: list[str]
    deliverables: list[dict[str, str]]
    handoff: dict[str, Any]
    guardrails: list[str]
    directory: Path
    prompt: str
    offline: dict[str, Any]
    max_context_chars: int | None = None


@dataclass
class GateRule:
    target: str
    after_attempt: int


@dataclass
class GateSpec:
    role: str
    verdict_field: str
    enabled: bool
    rules: list[GateRule]

    def rule_for(self, attempt: int) -> GateRule | None:
        candidates = [r for r in self.rules if attempt >= r.after_attempt]
        return max(candidates, key=lambda r: r.after_attempt) if candidates else None


@dataclass
class ProviderSpec:
    base_url: str
    model: str
    api_key: str
    timeout: int
    temperature: float
    api_key_source: str | None = None

    @property
    def available(self) -> bool:
        return bool(self.api_key.strip())


@dataclass
class TeamConfig:
    root: Path
    name: str
    roles: dict[str, RoleSpec]
    stages: list[RoleSpec]
    gates: dict[str, GateSpec]
    max_rework_cycles: int
    max_context_chars: int
    provider: ProviderSpec
    runs_dir: Path
    artifacts_dir: Path

    @property
    def protocol_text(self) -> str:
        path = self.root / "config" / "protocol.md"
        return path.read_text(encoding="utf-8") if path.is_file() else ""


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError:
        raise TeamError(f"缺少配置文件：{path}") from None
    except tomllib.TOMLDecodeError as exc:
        raise TeamError(f"配置文件格式错误：{path}：{exc}") from exc


def find_root(start: Path | None = None) -> Path:
    """按 环境变量 -> 代码位置 -> 当前目录向上 的顺序定位项目根目录。"""
    candidates: list[Path] = []
    env_home = os.environ.get("AI_SW_DEV_TEAM_HOME")
    if env_home:
        candidates.append(Path(env_home))
    candidates.append(Path(__file__).resolve().parents[2])
    current = (start or Path.cwd()).resolve()
    candidates.extend([current, *current.parents])
    for candidate in candidates:
        try:
            if (candidate / "config" / "team.toml").is_file():
                return candidate.resolve()
        except OSError:
            continue
    raise TeamError("未找到 config/team.toml；请设置 AI_SW_DEV_TEAM_HOME 或在项目根目录执行")


def _load_role(role_dir: Path) -> RoleSpec:
    data = _read_toml(role_dir / "role.toml")
    role_id = str(data.get("id") or role_dir.name).strip()
    name = str(data.get("name") or role_id).strip()

    prompt_path = role_dir / "prompt.md"
    if not prompt_path.is_file():
        raise TeamError(f"角色 {role_dir.name} 缺少 prompt.md 章程正文")
    prompt = prompt_path.read_text(encoding="utf-8").strip()

    offline_path = role_dir / "offline.toml"
    offline = _read_toml(offline_path) if offline_path.is_file() else {}

    deliverables = data.get("deliverables") or []
    if not isinstance(deliverables, list):
        raise TeamError(f"{role_dir.name}/role.toml 中 deliverables 必须是数组")
    normalized: list[dict[str, str]] = []
    for item in deliverables:
        if not isinstance(item, dict) or not item.get("path"):
            raise TeamError(f"{role_dir.name}/role.toml 中 deliverables 每项必须包含 path")
        normalized.append({str(k): str(v) for k, v in item.items()})

    return RoleSpec(
        id=role_id,
        name=name,
        order=int(data.get("order") or 0),
        gate=bool(data.get("gate", False)),
        responsibilities=[str(x) for x in data.get("responsibilities") or []],
        inputs=[str(x) for x in data.get("inputs") or []],
        outputs=[str(x) for x in data.get("outputs") or []],
        deliverables=normalized,
        handoff=dict(data.get("handoff") or {}),
        guardrails=[str(x) for x in data.get("guardrails") or []],
        directory=role_dir,
        prompt=prompt,
        offline=offline,
        max_context_chars=data.get("max_context_chars"),
    )


def _load_provider(root: Path, team_data: dict[str, Any]) -> ProviderSpec:
    values = dict(team_data.get("provider") or {})
    source: str | None = None

    local_path = root / "config" / "llm.local.toml"
    if local_path.is_file():
        local_values = _read_toml(local_path).get("provider") or {}
        if local_values.get("api_key"):
            source = "config/llm.local.toml"
        values.update(local_values)

    env_map = {
        "AI_SW_DEV_TEAM_API_KEY": "api_key",
        "AI_SW_DEV_TEAM_BASE_URL": "base_url",
        "AI_SW_DEV_TEAM_MODEL": "model",
        "AI_SW_DEV_TEAM_TIMEOUT": "timeout_seconds",
        "AI_SW_DEV_TEAM_TEMPERATURE": "temperature",
    }
    for env_name, key in env_map.items():
        raw = os.environ.get(env_name)
        if not raw:
            continue
        if key == "timeout_seconds":
            try:
                values[key] = int(raw)
            except ValueError:
                raise TeamError(f"环境变量 {env_name} 必须是整数秒") from None
        elif key == "temperature":
            try:
                values[key] = float(raw)
            except ValueError:
                raise TeamError(f"环境变量 {env_name} 必须是浮点数") from None
        else:
            values[key] = raw.strip()
        if key == "api_key":
            source = f"env:{env_name}"

    api_key = str(values.get("api_key") or "").strip()
    if PLACEHOLDER_KEY_MARK in api_key:
        api_key = ""
        source = "占位值未替换"

    return ProviderSpec(
        base_url=str(values.get("base_url") or DEFAULT_BASE_URL).rstrip("/"),
        model=str(values.get("model") or DEFAULT_MODEL),
        api_key=api_key,
        timeout=int(values.get("timeout_seconds") or 120),
        temperature=float(values.get("temperature", 0.3)),
        api_key_source=source,
    )


def _load_gates(team_data: dict[str, Any], stage_ids: set[str]) -> dict[str, GateSpec]:
    gates: dict[str, GateSpec] = {}
    for raw in team_data.get("gate") or []:
        role_id = str(raw.get("role") or "").strip()
        if not role_id:
            raise TeamError("门禁配置缺少 role 字段")
        if role_id not in stage_ids:
            raise TeamError(f"门禁角色 {role_id} 不在流水线 stages 中")
        rules: list[GateRule] = []
        for item in raw.get("on_fail") or []:
            target = str(item.get("target") or "").strip()
            if target not in stage_ids:
                raise TeamError(f"门禁回退目标 {target} 不在流水线 stages 中")
            rules.append(GateRule(target=target, after_attempt=int(item.get("after_attempt") or 1)))
        if not rules:
            raise TeamError(f"门禁 {role_id} 未配置 on_fail 回退规则")
        gates[role_id] = GateSpec(
            role=role_id,
            verdict_field=str(raw.get("verdict_field") or "verdict"),
            enabled=bool(raw.get("enabled", True)),
            rules=rules,
        )
    return gates


def load_team(root: Path | str | None = None) -> TeamConfig:
    root_path = Path(root).resolve() if root else find_root()
    team_data = _read_toml(root_path / "config" / "team.toml")
    team = team_data.get("team") or {}
    pipeline = team_data.get("pipeline") or {}
    paths = team_data.get("paths") or {}

    roles_dir = root_path / "roles"
    if not roles_dir.is_dir():
        raise TeamError(f"缺少角色目录：{roles_dir}")
    roles: dict[str, RoleSpec] = {}
    for role_dir in sorted(p for p in roles_dir.iterdir() if p.is_dir() and (p / "role.toml").is_file()):
        spec = _load_role(role_dir)
        if spec.id in roles:
            raise TeamError(f"角色 id 重复：{spec.id}")
        roles[spec.id] = spec

    ordered = sorted(roles.values(), key=lambda r: (r.order, r.id))
    for index, role in enumerate(ordered, start=1):
        if role.order <= 0:
            role.order = index

    stage_ids = [str(x) for x in pipeline.get("stages") or [r.id for r in ordered]]
    missing = [sid for sid in stage_ids if sid not in roles]
    if missing:
        raise TeamError(f"流水线引用了不存在的角色：{', '.join(missing)}")
    if len(set(stage_ids)) != len(stage_ids):
        raise TeamError("流水线 stages 中存在重复角色")
    stages = [roles[sid] for sid in stage_ids]

    return TeamConfig(
        root=root_path,
        name=str(team.get("name") or "ai_sw_dev_team"),
        roles=roles,
        stages=stages,
        gates=_load_gates(team_data, set(stage_ids)),
        max_rework_cycles=int(team.get("max_rework_cycles", 2)),
        max_context_chars=int(team.get("max_context_chars", 60000)),
        provider=_load_provider(root_path, team_data),
        runs_dir=root_path / str(paths.get("runs") or "runs"),
        artifacts_dir=root_path / str(paths.get("artifacts") or "artifacts"),
    )
