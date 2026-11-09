"""非交互式命令行入口：stdout 只输出结构化 JSON，进度写 stderr。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .config import TeamConfig, TeamError, load_team
from .orchestrator import Orchestrator

DEFAULT_DEMO_GOAL = "构建一个本地 Markdown 笔记管理小工具（增删查 + 索引生成），离线可运行"

EXIT_CODES = {"completed": 0, "blocked": 3, "failed": 1}


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _progress(message: str) -> None:
    print(f"[ai_sw_dev_team] {message}", file=sys.stderr, flush=True)


def _exit_code(status: str | None) -> int:
    return EXIT_CODES.get(status or "failed", 1)


def _cmd_run(
    root: str | None,
    goal: str,
    mode: str,
    max_rework: int | None,
    simulate_rework: bool,
    quiet: bool,
    source_run_id: str | None = None,
) -> int:
    config = load_team(root)
    orchestrator = Orchestrator(config)
    result = orchestrator.run(
        goal,
        mode=mode,
        simulate_rework=simulate_rework,
        max_rework_cycles=max_rework,
        source_run_id=source_run_id,
        progress=None if quiet else _progress,
    )
    _emit(result)
    return _exit_code(result.get("status"))


def _cmd_status(root: str | None, run_id: str) -> int:
    config = load_team(root)
    run_dir = config.runs_dir / run_id
    if not run_dir.is_dir():
        raise TeamError(f"未找到运行记录：{run_dir}")
    for name in ("result.json", "run.json"):
        path = run_dir / name
        if path.is_file():
            _emit(json.loads(path.read_text(encoding="utf-8")))
            return 0
    raise TeamError(f"运行记录缺少 result.json / run.json：{run_dir}")


def _cmd_list(root: str | None, limit: int) -> int:
    config = load_team(root)
    rows: list[dict] = []
    if config.runs_dir.is_dir():
        run_dirs = sorted((p for p in config.runs_dir.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True)
        for run_dir in run_dirs[: max(1, limit)]:
            data: dict = {}
            for name in ("result.json", "run.json"):
                path = run_dir / name
                if path.is_file():
                    data = json.loads(path.read_text(encoding="utf-8"))
                    break
            rows.append(
                {
                    "run_id": run_dir.name,
                    "status": data.get("status"),
                    "mode": data.get("mode"),
                    "goal": data.get("goal"),
                    "artifact_count": data.get("artifact_count"),
                    "finished_at": data.get("finished_at"),
                    "run_dir": str(run_dir),
                }
            )
    _emit({"runs": rows, "count": len(rows)})
    return 0


def _cmd_roles(root: str | None) -> int:
    config = load_team(root)
    _emit(
        {
            "team": config.name,
            "pipeline": [role.id for role in config.stages],
            "gate": {role: {"rules": [{"target": r.target, "after_attempt": r.after_attempt} for r in gate.rules]} for role, gate in config.gates.items()},
            "max_rework_cycles": config.max_rework_cycles,
            "roles": [
                {
                    "id": role.id,
                    "name": role.name,
                    "order": role.order,
                    "gate": role.gate,
                    "directory": str(role.directory),
                    "deliverables": role.deliverables,
                    "handoff": role.handoff,
                    "guardrails": role.guardrails,
                }
                for role in config.stages
            ],
        }
    )
    return 0


def _cmd_doctor(root: str | None) -> int:
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str, blocking: bool = False) -> None:
        checks.append({"check": name, "ok": ok, "blocking": blocking, "detail": detail})

    version = sys.version_info
    add(
        "python_version",
        version >= (3, 11),
        f"{version.major}.{version.minor}.{version.micro}（要求 >= 3.11）",
        blocking=True,
    )

    try:
        config: TeamConfig = load_team(root)
        add("config", True, f"根目录 {config.root}；团队 {config.name}", blocking=True)
    except TeamError as exc:
        add("config", False, str(exc), blocking=True)
        _emit({"status": "failed", "checks": checks})
        return 1

    add(
        "pipeline",
        len(config.stages) > 0,
        " -> ".join(role.id for role in config.stages),
        blocking=True,
    )

    missing_offline = [
        role.id
        for role in config.stages
        if not (role.offline.get("artifacts") or role.offline.get("fail_artifacts"))
    ]
    add(
        "offline_templates",
        not missing_offline,
        "全部角色均有离线模板" if not missing_offline else f"缺少离线模板：{', '.join(missing_offline)}",
    )

    add(
        "model_provider",
        config.provider.available,
        (
            f"已配置 {config.provider.model} @ {config.provider.base_url}（来源 {config.provider.api_key_source}）"
            if config.provider.available
            else "未配置 API Key，可使用离线演练模式（待配置项：AI_SW_DEV_TEAM_API_KEY 或 config/llm.local.toml）"
        ),
    )

    try:
        config.runs_dir.mkdir(parents=True, exist_ok=True)
        config.artifacts_dir.mkdir(parents=True, exist_ok=True)
        probe = config.runs_dir / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        add("runtime_dirs", True, f"{config.runs_dir}；{config.artifacts_dir}")
    except OSError as exc:
        add("runtime_dirs", False, f"运行时目录不可写：{exc}", blocking=True)

    blocking_failures = [item for item in checks if item["blocking"] and not item["ok"]]
    _emit({"status": "failed" if blocking_failures else "ok", "checks": checks})
    return 1 if blocking_failures else 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai_sw_dev_team", description="ai_sw_dev_team 多智能体协作团队 CLI")
    parser.add_argument("--root", help="项目根目录（默认自动查找）")
    parser.add_argument("--version", action="version", version=f"ai_sw_dev_team {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="执行一个团队目标，stdout 输出结构化 JSON")
    run.add_argument("--goal", required=True, help="团队目标")
    run.add_argument("--mode", choices=("auto", "llm", "offline"), default="auto")
    run.add_argument("--max-rework", type=int, default=None, help="覆盖最大返工次数")
    run.add_argument("--simulate-rework", action="store_true", help="离线模式模拟门禁失败以验证回退")
    run.add_argument("--quiet", action="store_true", help="不在 stderr 输出进度")

    demo = sub.add_parser("demo", help="离线演练：不调用模型跑通全流程")
    demo.add_argument("--goal", default=None, help=f"默认目标：{DEFAULT_DEMO_GOAL}")
    demo.add_argument("--simulate-rework", action="store_true", help="模拟首次质量不通过，验证回退链路")
    demo.add_argument("--quiet", action="store_true")

    status = sub.add_parser("status", help="查看指定运行的结构化结果")
    status.add_argument("--run-id", required=True)

    listing = sub.add_parser("list", help="列出最近的运行记录")
    listing.add_argument("--limit", type=int, default=20)

    sub.add_parser("roles", help="查看已加载的角色、流水线与门禁")
    sub.add_parser("doctor", help="自检配置、角色、离线模板与模型凭据")

    replay = sub.add_parser("replay", help="用历史运行的目标重放一次")
    replay.add_argument("--run-id", required=True)
    replay.add_argument("--mode", choices=("auto", "llm", "offline"), default="auto")
    replay.add_argument("--quiet", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "run":
            return _cmd_run(
                args.root,
                args.goal,
                args.mode,
                args.max_rework,
                args.simulate_rework,
                args.quiet,
            )
        if args.command == "demo":
            return _cmd_run(
                args.root,
                args.goal or DEFAULT_DEMO_GOAL,
                "offline",
                None,
                args.simulate_rework,
                args.quiet,
            )
        if args.command == "status":
            return _cmd_status(args.root, args.run_id)
        if args.command == "list":
            return _cmd_list(args.root, args.limit)
        if args.command == "roles":
            return _cmd_roles(args.root)
        if args.command == "doctor":
            return _cmd_doctor(args.root)
        if args.command == "replay":
            config = load_team(args.root)
            goal_path = config.runs_dir / args.run_id / "goal.md"
            if not goal_path.is_file():
                raise TeamError(f"运行记录 {args.run_id} 缺少 goal.md，无法重放")
            return _cmd_run(
                args.root,
                goal_path.read_text(encoding="utf-8").strip(),
                args.mode,
                None,
                False,
                args.quiet,
                source_run_id=args.run_id,
            )
        parser.error(f"未知命令：{args.command}")
        return 2
    except TeamError as exc:
        _emit({"status": "failed", "error": str(exc)})
        return 2
    except KeyboardInterrupt:
        _emit({"status": "interrupted", "error": "用户中断"})
        return 130
    except Exception as exc:  # noqa: BLE001
        _emit({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
