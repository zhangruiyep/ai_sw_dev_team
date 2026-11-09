"""编排器：接收目标 -> 分派任务 -> 传递产物 -> 质量门禁回退 -> 全程记录。"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Callable

from .config import RoleSpec, TeamConfig, TeamError
from .llm import OpenAICompatibleClient
from .models import StageOutcome, parse_structured
from .offline import OfflineEngine
from .roles import build_messages, truncate
from .store import RunStore, new_run_id, now_iso

ProgressFn = Callable[[str], None] | None


class Orchestrator:
    def __init__(self, config: TeamConfig):
        self.config = config
        self.client = OpenAICompatibleClient(config.provider)
        self.offline = OfflineEngine()

    # ---------- 对外主流程 ----------

    def run(
        self,
        goal: str,
        *,
        mode: str = "auto",
        simulate_rework: bool = False,
        max_rework_cycles: int | None = None,
        source_run_id: str | None = None,
        progress: ProgressFn = None,
    ) -> dict:
        goal = (goal or "").strip()
        if not goal:
            raise TeamError("团队目标不能为空")

        warnings: list[str] = []
        requested_mode = mode
        mode = self._resolve_mode(mode, warnings)
        max_cycles = self.config.max_rework_cycles if max_rework_cycles is None else max(0, max_rework_cycles)

        run_id = new_run_id()
        store = RunStore(self.config, run_id)
        store.init(
            goal,
            {
                "mode": mode,
                "requested_mode": requested_mode,
                "team": self.config.name,
                "source_run_id": source_run_id,
            },
        )
        store.event("run_start", run_id=run_id, mode=mode, goal=goal, simulate_rework=simulate_rework)
        self._notify(progress, f"run_id={run_id} 模式={mode} 团队={self.config.name}")

        started = time.monotonic()
        stage_records: list[dict] = []
        artifacts_manifest: list[dict] = []
        latest_artifacts: dict[str, list[tuple[str, str]]] = {}
        attempts: dict[str, int] = {role.id: 0 for role in self.config.stages}

        feedback: str | None = None
        cycles = 0
        index = 0
        status = "completed"
        error: str | None = None

        while index < len(self.config.stages):
            role = self.config.stages[index]
            attempts[role.id] += 1
            attempt = attempts[role.id]

            upstream = self._collect_upstream(role, latest_artifacts)
            self._notify(progress, f"[{index + 1}/{len(self.config.stages)}] {role.name} · 第 {attempt} 次尝试")
            store.event("stage_start", role=role.id, attempt=attempt)

            stage_started = time.monotonic()
            try:
                outcome = self._execute(role, goal, attempt, upstream, feedback, mode, simulate_rework, store)
            except Exception as exc:  # noqa: BLE001 - 运行期任何失败都要转成结构化结果
                elapsed = round(time.monotonic() - stage_started, 3)
                message = f"{role.name} 执行失败：{exc}"
                status = "failed"
                error = message
                stage_records.append(
                    {
                        "order": role.order,
                        "role": role.id,
                        "name": role.name,
                        "attempt": attempt,
                        "status": "error",
                        "verdict": None,
                        "summary": None,
                        "duration_seconds": elapsed,
                        "stage_dir": self._relative(store.stage_dir(role, attempt)),
                        "artifacts": [],
                        "error": str(exc),
                    }
                )
                store.event("stage_error", role=role.id, attempt=attempt, error=str(exc))
                self._notify(progress, message)
                break

            elapsed = round(time.monotonic() - stage_started, 3)
            saved = store.save_artifacts(role, outcome.artifacts)
            artifacts_manifest.extend(saved)
            latest_artifacts[role.id] = outcome.artifacts

            meta = {
                "role": role.id,
                "name": role.name,
                "attempt": attempt,
                "mode": mode,
                "duration_seconds": elapsed,
                "started_at": now_iso(),
                "verdict": outcome.verdict,
                "handoff_ready": outcome.handoff_ready,
            }
            store.write_stage(role, attempt, outcome, meta)
            for saved_item in saved:
                store.event(
                    "artifact_written",
                    role=role.id,
                    attempt=attempt,
                    path=saved_item["path"],
                    size=saved_item["size"],
                    sha256=saved_item["sha256"],
                )

            record = {
                "order": role.order,
                "role": role.id,
                "name": role.name,
                "attempt": attempt,
                "status": "ok",
                "verdict": outcome.verdict,
                "summary": outcome.summary,
                "handoff_ready": outcome.handoff_ready,
                "duration_seconds": elapsed,
                "stage_dir": self._relative(store.stage_dir(role, attempt)),
                "artifacts": [item["path"] for item in saved],
                "warnings": outcome.warnings,
            }
            stage_records.append(record)
            warnings.extend(f"{role.name}：{warning}" for warning in outcome.warnings)
            if not outcome.handoff_ready:
                warnings.append(f"{role.name} 声明交接条件未完全满足（{outcome.handoff_notes or '无说明'}）")
            store.event(
                "stage_end",
                role=role.id,
                attempt=attempt,
                verdict=outcome.verdict,
                summary=outcome.summary,
                duration_seconds=elapsed,
            )
            self._notify(
                progress,
                f"  -> {role.name} 完成：verdict={outcome.verdict or 'n/a'}，产物 {len(saved)} 个",
            )

            gate = self.config.gates.get(role.id)
            gate_failed = False
            if gate and gate.enabled:
                if outcome.verdict == "fail":
                    gate_failed = True
                elif outcome.verdict is None:
                    warnings.append(f"门禁角色 {role.name} 未返回 verdict，按通过处理")

            if gate_failed:
                rule = gate.rule_for(attempt)
                if rule and cycles < max_cycles:
                    cycles += 1
                    feedback = self._compose_feedback(role, outcome)
                    target_index = self._stage_index(rule.target)
                    store.decision(
                        "rework",
                        gate=role.id,
                        attempt=attempt,
                        target=rule.target,
                        cycle=cycles,
                        max_cycles=max_cycles,
                        reason=outcome.summary,
                    )
                    store.event("gate_failed", role=role.id, attempt=attempt, target=rule.target, cycle=cycles)
                    self._notify(
                        progress,
                        f"  -> 质量门禁未通过，回退到 {rule.target}（第 {cycles}/{max_cycles} 次返工）",
                    )
                    index = target_index
                    continue

                status = "blocked"
                error = (
                    f"{role.name} 未通过且无可用返工额度"
                    f"（已用 {cycles}/{max_cycles}，规则目标：{rule.target if rule else '无'}）"
                )
                store.decision(
                    "gate_blocked",
                    gate=role.id,
                    attempt=attempt,
                    cycle=cycles,
                    max_cycles=max_cycles,
                    reason=outcome.summary,
                )
                store.event("gate_blocked", role=role.id, attempt=attempt, reason=outcome.summary)
                self._notify(progress, "  -> 门禁失败且返工额度耗尽，整轮终止（未进入交付）")
                break

            if gate and gate.enabled:
                store.event("gate_passed", role=role.id, attempt=attempt)
                feedback = None

            index += 1

        finished_at = now_iso()
        duration = round(time.monotonic() - started, 3)
        result = {
            "run_id": run_id,
            "team": self.config.name,
            "goal": goal,
            "status": status,
            "mode": mode,
            "requested_mode": requested_mode,
            "started_at": store.started_at,
            "finished_at": finished_at,
            "duration_seconds": duration,
            "rework_cycles": cycles,
            "stages": stage_records,
            "artifacts": artifacts_manifest,
            "artifact_count": len(artifacts_manifest),
            "run_dir": str(store.dir),
            "artifacts_dir": str(store.artifacts_root),
            "provider": (
                {"base_url": self.config.provider.base_url, "model": self.config.provider.model}
                if mode == "llm"
                else None
            ),
            "warnings": warnings,
            "error": error,
            "source_run_id": source_run_id,
        }
        store.write_manifest(artifacts_manifest)
        store.finish(result)
        store.event("run_end", status=status, duration_seconds=duration, artifact_count=len(artifacts_manifest))
        self._notify(progress, f"完成：status={status}，产物 {len(artifacts_manifest)} 个 -> {store.artifacts_root}")
        return result

    # ---------- 内部工具 ----------

    def _resolve_mode(self, mode: str, warnings: list[str]) -> str:
        if mode == "auto":
            if self.client.available:
                return "llm"
            warnings.append(
                "未检测到可用 API Key，已自动使用离线演练模式（待配置项：AI_SW_DEV_TEAM_API_KEY 或 config/llm.local.toml）"
            )
            return "offline"
        if mode == "llm":
            if self.client.available:
                return "llm"
            warnings.append("已指定模型模式但缺少 API Key，已降级为离线演练模式（待配置项）")
            return "offline"
        if mode == "offline":
            return "offline"
        raise TeamError(f"不支持的运行模式：{mode}")

    def _execute(
        self,
        role: RoleSpec,
        goal: str,
        attempt: int,
        upstream: str,
        feedback: str | None,
        mode: str,
        simulate_rework: bool,
        store: RunStore,
    ) -> StageOutcome:
        limit = role.max_context_chars or self.config.max_context_chars
        system, user = build_messages(
            role=role,
            goal=goal,
            upstream=upstream,
            feedback=feedback,
            protocol=self.config.protocol_text,
            attempt=attempt,
            run_id=store.run_id,
            max_context_chars=limit,
        )
        prompt_doc = f"# SYSTEM\n\n{system}\n\n# USER\n\n{user}"
        if mode == "offline":
            prompt_doc = "# 离线演练模式：以下为拟发送给模型的提示词（未实际调用）\n\n" + prompt_doc
        store.write_stage_input(role, attempt, prompt_doc)

        if mode == "offline":
            ctx = {
                "goal": goal,
                "run_id": store.run_id,
                "attempt": attempt,
                "feedback": feedback or "",
                "date": datetime.now().strftime("%Y-%m-%d"),
                "role_name": role.name,
            }
            return self.offline.execute(role, ctx, attempt, simulate_rework)

        raw = self.client.chat(system, user)
        return parse_structured(raw, role)

    def _collect_upstream(self, role: RoleSpec, latest: dict[str, list[tuple[str, str]]]) -> str:
        blocks: list[str] = []
        for spec in self.config.stages:
            if spec.order >= role.order:
                break
            for path, content in latest.get(spec.id, []):
                blocks.append(f"## {spec.name} · {path}\n\n{content}")
        return truncate("\n\n".join(blocks), role.max_context_chars or self.config.max_context_chars)

    def _compose_feedback(self, role: RoleSpec, outcome: StageOutcome) -> str:
        chunks = [f"【{role.name} 判定：{outcome.verdict}】{outcome.summary}"]
        if outcome.decisions:
            chunks.append("关键问题与决策：\n" + "\n".join(f"- {item}" for item in outcome.decisions))
        for path, content in outcome.artifacts:
            chunks.append(f"### {path}\n{content}")
        return truncate("\n\n".join(chunks), self.config.max_context_chars // 2)

    def _stage_index(self, role_id: str) -> int:
        for index, role in enumerate(self.config.stages):
            if role.id == role_id:
                return index
        raise TeamError(f"回退目标 {role_id} 不在流水线中")

    def _relative(self, path) -> str:
        try:
            return path.relative_to(self.config.root).as_posix()
        except ValueError:
            return str(path)

    def _notify(self, progress: ProgressFn, message: str) -> None:
        if progress is not None:
            progress(message)
