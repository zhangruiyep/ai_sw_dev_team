"""运行目录与交付物落盘：保证全过程可追溯、可重放。

目录约定：
  runs/<run_id>/goal.md
  runs/<run_id>/run.json
  runs/<run_id>/events.jsonl
  runs/<run_id>/decisions.jsonl
  runs/<run_id>/stages/<序号-角色>/attempt-<n>/{input.md,output.md,structured.json,meta.json}
  runs/<run_id>/result.json
  artifacts/<run_id>/<角色>/<产物路径>
  artifacts/<run_id>/manifest.json
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path

from .config import RoleSpec, TeamConfig, TeamError
from .models import StageOutcome


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def new_run_id() -> str:
    return f"run_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"


class RunStore:
    def __init__(self, config: TeamConfig, run_id: str):
        self.config = config
        self.run_id = run_id
        self.dir = config.runs_dir / run_id
        self.artifacts_root = config.artifacts_dir / run_id
        self.started_at = now_iso()

    # ---------- 基础写入 ----------

    def write_text(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _append_jsonl(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"ts": now_iso(), **payload}, ensure_ascii=False) + "\n")

    def event(self, name: str, **fields) -> None:
        self._append_jsonl(self.dir / "events.jsonl", {"event": name, **fields})

    def decision(self, kind: str, **fields) -> None:
        self._append_jsonl(self.dir / "decisions.jsonl", {"decision": kind, **fields})

    def update_run(self, **fields) -> None:
        path = self.dir / "run.json"
        data: dict = {}
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}
        data.update({key: value for key, value in fields.items() if value is not None})
        data.setdefault("run_id", self.run_id)
        self.write_text(path, json.dumps(data, ensure_ascii=False, indent=2))

    # ---------- 运行初始化 ----------

    def init(self, goal: str, meta: dict) -> None:
        (self.dir / "stages").mkdir(parents=True, exist_ok=True)
        self.artifacts_root.mkdir(parents=True, exist_ok=True)
        self.write_text(self.dir / "goal.md", goal)
        self.update_run(goal=goal, status="running", started_at=self.started_at, **meta)

    # ---------- 阶段记录 ----------

    def stage_dir(self, role: RoleSpec, attempt: int) -> Path:
        return self.dir / "stages" / f"{role.order:02d}-{role.id}" / f"attempt-{attempt}"

    def write_stage_input(self, role: RoleSpec, attempt: int, text: str) -> Path:
        path = self.stage_dir(role, attempt) / "input.md"
        self.write_text(path, text)
        return path

    def write_stage(self, role: RoleSpec, attempt: int, outcome: StageOutcome, meta: dict) -> Path:
        base = self.stage_dir(role, attempt)
        self.write_text(base / "output.md", outcome.raw or outcome.summary)
        self.write_text(
            base / "structured.json",
            json.dumps(
                {
                    "role": role.id,
                    "attempt": attempt,
                    "summary": outcome.summary,
                    "decisions": outcome.decisions,
                    "verdict": outcome.verdict,
                    "handoff": {"ready": outcome.handoff_ready, "notes": outcome.handoff_notes},
                    "risks": outcome.risks,
                    "artifacts": [path for path, _ in outcome.artifacts],
                    "warnings": outcome.warnings,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        self.write_text(base / "meta.json", json.dumps(meta, ensure_ascii=False, indent=2))
        # 每次尝试的产物原件留档，返工后旧版本仍可追溯
        for raw_path, content in outcome.artifacts:
            rel = self._safe_rel(raw_path)
            self.write_text(base / "artifacts" / rel, content)
        return base

    # ---------- 交付物 ----------

    def _safe_rel(self, raw: str) -> Path:
        rel = Path(str(raw).replace("\\", "/"))
        if rel.is_absolute() or ".." in rel.parts or not rel.parts:
            raise TeamError(f"非法的产物路径：{raw}")
        return rel

    def save_artifacts(self, role: RoleSpec, artifacts: list[tuple[str, str]]) -> list[dict]:
        saved: list[dict] = []
        for raw_path, content in artifacts:
            rel = self._safe_rel(raw_path)
            target = self.artifacts_root / role.id / rel
            self.write_text(target, content)
            saved.append(
                {
                    "path": f"{role.id}/{rel.as_posix()}",
                    "abs_path": str(target),
                    "role": role.id,
                    "size": len(content.encode("utf-8")),
                    "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                }
            )
        return saved

    # ---------- 收尾 ----------

    def write_manifest(self, artifacts: list[dict]) -> None:
        self.write_text(
            self.artifacts_root / "manifest.json",
            json.dumps(
                {"run_id": self.run_id, "generated_at": now_iso(), "artifacts": artifacts},
                ensure_ascii=False,
                indent=2,
            ),
        )

    def finish(self, result: dict) -> None:
        self.write_text(self.dir / "result.json", json.dumps(result, ensure_ascii=False, indent=2))
        self.update_run(status=result.get("status"), finished_at=result.get("finished_at"))
