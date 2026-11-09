# ai_sw_dev_team — 本地可运行的多智能体协作团队

ai_sw_dev_team 是一个**角色固定、流程固定、产物落盘**的多智能体协作框架：五个角色按流水线协作，
由编排器负责分派、传递产物、质量门禁回退，并把全过程写入 `runs/<run_id>/`，
交付物写入 `artifacts/<run_id>/`，同一套团队可以反复用于不同目标。

| 顺序 | 角色 | id | 主要交付 |
| --- | --- | --- | --- |
| 1 | 需求分析 | `requirements` | 需求说明、验收标准 |
| 2 | 软件架构和方案 | `architecture` | 架构说明、决策记录、任务拆解 |
| 3 | 软件开发 | `development` | 实现说明、源码、测试 |
| 4 | 软件质量控制（门禁） | `quality` | 质量报告、缺陷清单 |
| 5 | 交付和版本管理 | `release` | 发布说明、检查单、版本清单、交付摘要 |

## 1. 快速开始

环境要求：Python 3.11+（本机 3.14.5 已验证）、Git；**不需要** Docker，**不需要**任何第三方 Python 包。

```powershell
cd E:\WORK\ai_sw_dev_team

# 自检：配置、角色、离线模板、模型凭据、运行时目录
.\ai_sw_dev_team.cmd doctor

# 离线演练：不调用模型，跑通全流程（默认目标是一个本地笔记小工具）
.\ai_sw_dev_team.cmd demo --simulate-rework

# 执行真实目标（有 API Key 时走模型；没有则自动降级为离线演练）
.\ai_sw_dev_team.cmd run --goal "用 Python 做一个把 CSV 转成 Markdown 表格的命令行工具"

# 查看结果
.\ai_sw_dev_team.cmd list
.\ai_sw_dev_team.cmd status --run-id <run_id>
```

> stdout **只输出一个 JSON 文档**，便于外部程序解析；进度信息写入 stderr。
> 也可以使用 `python -m ai_sw_dev_team`（需 `PYTHONPATH=src`），或 `pip install -e .` 后直接使用 `ai_sw_dev_team` 命令。
> 为兼容早期按 `team ...` 调用它的脚本，根目录保留了 `team.cmd`，它只是转发到 `ai_sw_dev_team.cmd`。

退出码：`0` 成功完成、`1` 执行失败、`2` 参数/配置错误、`3` 质量门禁拦截（未进入交付）。

## 2. 模型配置

支持任意 OpenAI 兼容的 `/chat/completions` 接口。凭据**绝不写入仓库**，两种方式任选：

### 方式 A：环境变量（推荐，优先级最高）

```powershell
$env:AI_SW_DEV_TEAM_API_KEY="sk-..."
$env:AI_SW_DEV_TEAM_BASE_URL="https://api.openai.com/v1"   # 可换成其他兼容服务
$env:AI_SW_DEV_TEAM_MODEL="gpt-4o-mini"
# 可选：AI_SW_DEV_TEAM_TIMEOUT=180、AI_SW_DEV_TEAM_TEMPERATURE=0.2
.\ai_sw_dev_team.cmd run --goal "..."
```

### 方式 B：本地配置文件

```powershell
Copy-Item config\llm.example.toml config\llm.local.toml
# 编辑 config\llm.local.toml 填入 base_url / model / api_key
```

`config/llm.local.toml` 已被 `.gitignore` 忽略；`config/team.toml` 只保存非敏感默认值。

优先级：环境变量 > `config/llm.local.toml` > `config/team.toml`。

**缺少 API Key 时不会卡死**：`--mode auto`（默认）会自动切换离线演练模式，并在结果 JSON 的
`warnings` 中把它记为待配置项；`--mode llm` 在缺 Key 时同样降级并给出警告。

## 3. 命令一览

| 命令 | 说明 |
| --- | --- |
| `ai_sw_dev_team.cmd run --goal "..."` | 执行一个团队目标 |
| `ai_sw_dev_team.cmd demo [--simulate-rework]` | 离线演练，不调用模型 |
| `ai_sw_dev_team.cmd status --run-id <id>` | 输出该轮的结构化结果（优先 `result.json`） |
| `ai_sw_dev_team.cmd list [--limit N]` | 列出最近运行记录 |
| `ai_sw_dev_team.cmd roles` | 查看已加载的角色、流水线与门禁规则 |
| `ai_sw_dev_team.cmd replay --run-id <id>` | 用历史目标重放一轮（新 `run_id`，记录 `source_run_id`） |
| `ai_sw_dev_team.cmd doctor` | 自检环境与配置 |

常用参数：`--mode auto|llm|offline`、`--max-rework N`、`--quiet`、`--root <项目根>`。

`run` 输出的 JSON 关键字段：

| 字段 | 含义 |
| --- | --- |
| `run_id` | 本轮唯一标识，也是 `runs/` 与 `artifacts/` 下的目录名 |
| `status` | `completed` / `failed` / `blocked` |
| `mode` | 实际使用的模式（`llm` / `offline`）与 `requested_mode` |
| `stages[]` | 每个阶段：角色、第几次尝试、verdict、耗时、产物相对路径、阶段记录目录 |
| `artifacts[]` | 全部交付物：相对路径、绝对路径、大小、sha256 |
| `run_dir` / `artifacts_dir` | 本轮记录目录与交付物目录 |
| `rework_cycles` | 实际发生的返工次数 |
| `warnings` / `error` | 待配置项、交接提示、失败原因 |

## 4. 目录结构

```
E:\WORK\ai_sw_dev_team\
├─ ai_sw_dev_team.cmd           # Windows 主入口（设置 PYTHONPATH 后调用 python -m ai_sw_dev_team）
├─ team.cmd                     # 兼容入口：转发到 ai_sw_dev_team.cmd
├─ pyproject.toml               # 可选安装配置（pip install -e . 后可用 ai_sw_dev_team 命令）
├─ config\
│  ├─ team.toml                 # 团队名、流水线顺序、质量门禁、返工上限、模型默认值、路径
│  ├─ protocol.md               # 所有角色共用的输出协议（JSON 契约）
│  └─ llm.example.toml          # 本地凭据模板（复制为 llm.local.toml，已被忽略）
├─ roles\
│  ├─ 01-requirements\          # role.toml（结构化契约）+ prompt.md（章程正文）+ offline.toml（演练模板）
│  ├─ 02-architecture\
│  ├─ 03-development\
│  ├─ 04-quality\               # gate = true，质量门禁角色
│  └─ 05-release\
├─ src\ai_sw_dev_team\
│  ├─ config.py                 # 配置加载与校验（team.toml / role.toml / 凭据优先级）
│  ├─ roles.py                  # 章程渲染与提示词组装（提示词不硬编码在代码里）
│  ├─ models.py                 # 结构化输出解析（容忍 Markdown 包裹）
│  ├─ llm.py                    # OpenAI 兼容客户端（urllib，无第三方依赖）
│  ├─ offline.py                # 离线演练引擎（模板驱动）
│  ├─ store.py                  # run/artifact 落盘、事件流、决策流、manifest
│  ├─ orchestrator.py           # 编排核心：分派、上下文传递、门禁回退
│  └─ cli.py                    # 非交互式 CLI，stdout 输出 JSON
├─ runs\                        # 运行时生成：每轮全链路记录（已 gitignore）
├─ artifacts\                   # 运行时生成：每轮交付物（已 gitignore）
└─ examples\rework-demo\        # 一次完整的演练示例（含 runs/ 与 artifacts/ 目录）
```

## 5. 运行记录与产物（可追溯、可重放）

```
runs/<run_id>/
├─ goal.md                      # 原始目标
├─ run.json                     # 运行元信息与状态
├─ events.jsonl                 # 事件流：run_start / stage_start / artifact_written / gate_failed / run_end
├─ decisions.jsonl              # 决策流：返工、门禁拦截及原因
├─ stages/
│  └─ 03-development/attempt-2/
│     ├─ input.md               # 该阶段收到的完整提示词（目标 + 上游产物 + 返工反馈）
│     ├─ output.md              # 模型原始输出（离线模式为演练标记）
│     ├─ structured.json        # 结构化结论：summary/decisions/verdict/handoff/risks
│     ├─ meta.json              # 角色、尝试次数、模式、耗时
│     └─ artifacts/             # 本次尝试的产物原件（返工后旧版本仍可查）
├─ result.json                  # 对外输出的同一份结构化结果
└─ (最终交付物位于独立目录 artifacts/<run_id>/，见下)
```

`artifacts/<run_id>/<角色>/<产物路径>` 是该轮**最终交付物**；
`artifacts/<run_id>/manifest.json` 保存全部产物的路径、大小与 sha256。

## 6. 如何新增角色或调整流程

**新增角色**（例如"安全评审"放在质量控制之后）：

1. 新建 `roles/06-security/`，包含：
   - `role.toml`：`id`、`name`、`order`、`responsibilities`、`inputs`、`outputs`、
     `deliverables`（含 `path` 与 `description`）、`handoff.ready_when`、`guardrails`；
     需要做门禁时加 `gate = true`。
   - `prompt.md`：角色章程正文（你是谁、工作方法、边界、输出要求）。
   - `offline.toml`：离线演练模板（`summary`、`decisions`、`[[artifacts]]`；门禁角色还可写
     `fail_summary` / `fail_artifacts`）。
2. 在 `config/team.toml` 的 `pipeline.stages` 中按顺序加入 `security`。
3. 如需门禁，在 `[[gate]]` 中增加对应角色与 `on_fail` 回退规则。

**调整流程**：

- 调整顺序：改 `pipeline.stages`（`order` 用于上下文归属，建议保持一致）。
- 调整返工上限：改 `team.max_rework_cycles`，或运行时 `--max-rework`。
- 调整回退策略：改 `[[gate.on_fail]]`，例如只在第 2 次失败后退回架构，或让质量门禁直接退回需求。
- 调整上下文预算：`team.max_context_chars`，或在角色 `role.toml` 中单独设置 `max_context_chars`。

## 7. 角色输出协议

所有角色只输出一个 JSON 对象（完整定义见 `config/protocol.md`）：

```json
{
  "summary": "一句话结论",
  "decisions": ["关键决策"],
  "verdict": "pass | fail | n/a",
  "artifacts": [{"path": "requirements.md", "content": "文件全文"}],
  "handoff": {"ready": true, "notes": "交接说明"},
  "risks": ["风险与未决项"]
}
```

编排器校验后落盘；产物路径禁止绝对路径与 `..`；模型返回非 JSON 时降级为单文件产物并在
`warnings` 中提示（不会中断整轮）。

## 8. 设计取舍与真实分歧（按合理默认推进）

1. **串行 vs 并行**：默认串行。质量门禁必须读到完整上游产物，并行会让"合并/冲突裁决"变成新的
   角色与流程，当前规模收益低。若要并行，建议在架构阶段后加"集成合并"阶段，并先定共识规则。
2. **回退规则**：默认第 1 次质量问题退回开发、第 2 次退回架构、超过 2 次整轮 `blocked`。
   这是"修复成本最小"的默认；如果你更看重严格性，可把规则改为直接退回需求分析。
3. **配置格式选 TOML**：`tomllib` 是 Python 3.11+ 标准库，读写零依赖；同时支持多行字符串，
   适合承载章程与代码示例。YAML 需要第三方依赖，JSON 不适合长文本。
4. **离线模式是模板驱动，不是模拟模型**：它验证的是编排逻辑、门禁回退、落盘与 CLI 契约；
   内容本身是示例数据，接入模型后由角色真实生成。
5. **技术栈**：Python 标准库足够覆盖配置、HTTP、落盘与 CLI；换 Node 只会增加部署面，
   且当前无性能瓶颈。

## 9. 演练示例

`examples/rework-demo/` 保存了一轮完整演练（`run_id = run_20261109_165912_c35e4b`）：

- `examples/rework-demo/runs/<run_id>/`：目标、事件流、决策流、每次尝试的输入/输出/产物原件
- `examples/rework-demo/artifacts/<run_id>/`：14 个最终交付文件 + `manifest.json`（结果中 19 条产物记录含返工重写）
- 该轮刻意让质量门禁首次失败：`quality` 第 1 次 `fail` → 回退 `development` 第 2 次 →
  `quality` 第 2 次 `pass` → `release`，`decisions.jsonl` 中有 `rework` 记录。

对应本地目录还可直接查看 `runs/run_20261109_165912_c35e4b/` 与 `artifacts/run_20261109_165912_c35e4b/`。

## 10. 故障排查

| 现象 | 处理 |
| --- | --- |
| `未找到 config/team.toml` | 在项目根执行，或设置 `AI_SW_DEV_TEAM_HOME=E:\WORK\ai_sw_dev_team` |
| 结果 `mode=offline` 且 `warnings` 提示缺 Key | 按第 2 节配置凭据 |
| 模型接口返回 HTTP 4xx/5xx | 检查 `base_url`、`model`、密钥与额度；`doctor` 可快速确认 |
| 质量门禁反复失败 | 查看 `decisions.jsonl` 与质量角色的 `defect_list.md`，按回退目标逐条修复 |
| 产物路径非法 | 模型返回了绝对路径或 `..`；编排器会拒绝并中止该轮 |
