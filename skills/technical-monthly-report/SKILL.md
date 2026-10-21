---
name: technical-monthly-report
version: 1.1.0
description: 基于工作流的可复用技术月报 Skill；从 GitHub 提交与本地 Git 笔记提炼技术点并生成结构化中文月报。
triggers:
  - 生成技术月报
  - 总结本月 GitHub 工作和学习笔记
  - 根据 commits 和笔记输出复盘报告
  - 复盘指定时间范围内开发活动
---

# Technical Monthly Report Skill（Workflow-Based）

## 1. Skill 定位

这是 **workflow-based skill**，对外提供“生成技术月报”的稳定能力。

- ✅ 可直接使用：`SKILL.md`、`manifest.yaml`、`examples/input.yaml`、`prompts/generate_report.md`、`references/workflow.md`
- ⚠️ 逻辑节点（如 `fetch_github_commits`）是**工作流节点名**，不是本目录内可直接 import 的 Python API
- ❌ 本 Skill 包**不内置可执行 Python 入口函数**（例如 `generate_monthly_report()`）

## 2. 运行模式

### 模式 A：Agent 原生执行（推荐）
由支持 Skill 的 Agent/运行时读取本目录文档，按工作流执行并返回结果。

### 模式 B：Python/LangGraph 适配（可选）
本目录提供 `references/workflow.md` 作为适配参考契约（逻辑节点与 I/O）。
若你要自行实现 Python/LangGraph，请将该文档视为契约说明，而非现成代码。

## 3. 输入契约（Input Contract）

```yaml
input:
  time_range:
    since: "YYYY-MM-DD"
    until: "YYYY-MM-DD"
  github:
    repo: "owner/repo"
    username: "github-username"
    include_diff: true                # 可选，默认 true
    max_files_per_commit: 3           # 可选，默认 3
    max_chars_per_file_patch: 1200    # 可选，默认 1200
  notes:
    directory: "/absolute/path/to/notes-git-repo"
    max_chars_per_note: 4000          # 可选，默认 4000
    include_extensions:               # 可选，默认 [.md, .markdown, .txt]
      - ".md"
      - ".markdown"
      - ".txt"
  constraints:                        # 可选
    max_fetch_rounds: 3               # 默认 3
    min_tech_points: 3                # 默认 3
    language: "zh-CN"                # 默认 zh-CN
  output:                             # 可选
    output_path: "docs/report_YYYY-MM-DD.md"
    write_file: true                  # 默认 true
```

### 必填字段
- `time_range.since`
- `time_range.until`
- `github.repo`
- `github.username`
- `notes.directory`

## 4. 输出契约（Output Contract）

```yaml
status: success | partial | failed
report_markdown: string
output_path: string | null
stats:
  commit_count: integer
  note_file_count: integer
  note_content_count: integer
  tech_point_count: integer
  deepen_rounds: integer
warnings: string[]
errors: string[]
```

### 结果语义
- `success`：流程完成，报告生成成功（可写文件或仅内存返回）
- `partial`：部分数据源不可用，但仍产出报告（必须给出 `warnings`）
- `failed`：关键前置条件不满足或生成失败（必须给出 `errors`）

## 5. 工作流逻辑（逻辑节点，不等于可调用函数）

```text
START
  ├─ fetch_github_commits
  │    └─ fetch_commit_diffs
  └─ scan_notes_in_time_range
       └─ parse_note_files

两条链路汇合
  ↓
analyze_tech_points
  ↓
route_after_analysis
  ├─ sufficient → generate_report → save_report → END
  └─ deepen     → fetch_commit_diffs → analyze_tech_points
```

## 6. 终止条件与降级策略

### 终止条件
满足任一条件即终止循环并进入报告生成/结束：
1. 技术点充分（数量与字段完整性满足阈值）
2. 已达 `max_fetch_rounds`
3. 无可继续深挖的数据
4. 外部数据源不可访问且无新增输入

### 降级策略
- GitHub 不可访问：继续尝试 notes 数据源，`status=partial` 并写入 warnings
- notes 目录不可访问：继续尝试 GitHub 数据源，`status=partial` 并写入 warnings
- 两个数据源都不可用：`status=failed`，返回 errors，禁止伪造成功
- 单条 commit/单篇笔记读取失败：跳过该条并记录 warnings

## 7. 外部能力与权限要求

1. GitHub API
   - 能力：读取提交记录与 commit diff
   - 权限：目标仓库只读权限（私有仓库需 token）
2. 本地 Git 笔记仓库
   - 能力：执行 `git ls-files`、`git log`，读取文件正文
   - 权限：本地目录读权限 + git 可执行
3. LLM 能力
   - 能力：技术点抽取与月报生成
   - 权限：可访问模型服务（通过运行时注入 API Key）

> 所有密钥（如 API Key/Token）必须通过运行时环境或工具注入，不写入 Skill 文件。

## 8. 质量检查清单

返回前应满足：
- [ ] 时间范围已显式化为具体日期
- [ ] 输出包含 3 个固定章节：已做任务 / 技术难点与解决方案 / 未来改进方向
- [ ] `warnings` 与 `errors` 与实际执行一致
- [ ] 未将推测表述为事实
- [ ] 循环可证明终止（轮数上限生效）
- [ ] 输出字段与本契约一致

## 9. 目录内真实文件映射

- `./SKILL.md`：Skill 主契约（本文件）
- `./README.md`：面向使用者的完整操作说明
- `./manifest.yaml`：可移植清单
- `./references/workflow.md`：工作流逻辑与节点契约参考
- `./prompts/generate_report.md`：报告生成提示模板
- `./examples/input.yaml`：最小可用输入与扩展示例
