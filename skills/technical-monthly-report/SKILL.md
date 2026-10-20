---
name: technical-monthly-report
version: 1.0.0
description: 从 GitHub 提交记录、本地 Git Markdown 笔记和代码 diff 中提炼技术工作，生成结构化中文技术月报。内部采用工作流编排，支持并行采集、条件质量关卡、有限深挖循环、Token 预算和 Markdown 输出。
triggers:
  - 生成技术月报
  - 总结本月 GitHub 工作和学习笔记
  - 根据 commits 和 Obsidian 笔记生成报告
  - 复盘指定时间范围内的开发活动
---

# Technical Monthly Report Skill

## 目标

将指定时间范围内的 GitHub 活动和本地学习笔记转换为可追溯、结构化的技术月报。

本 Skill 是一个**工作流型 Skill**，不是开放式自主 Agent。它允许内部使用 LangGraph、条件边、循环和工具调用，但对外暴露的是稳定的“生成技术月报”能力。

## 什么时候使用

当用户需要以下任务时使用本 Skill：

- 生成月报、周报或阶段性技术报告；
- 总结 GitHub commits、代码 diff 和本地学习笔记；
- 提炼技术难点、解决方案和后续改进；
- 对指定时间范围的个人开发活动进行复盘。

如果用户没有给出时间范围，应先询问时间范围；如果用户明确表示“本月”，使用当前日期所在月份，并在结果中写出具体起止日期。

## 输入契约

至少需要：

```yaml
time_range:
  since: YYYY-MM-DD
  until: YYYY-MM-DD
github:
  repo: owner/name
  username: username
notes:
  directory: /absolute/path/to/git-notes-repository
```

可选参数：

```yaml
output_path: docs/report_YYYY-MM-DD.md
language: zh-CN
max_fetch_rounds: 3
min_tech_points: 3
include_diff: true
max_chars_per_note: 4000
max_chars_per_file_patch: 1200
max_files_per_commit: 3
```

如果无法访问本地目录，不要假装读取成功；应退化为仅使用 GitHub 数据，并在报告元信息或 warnings 中说明。

## 输出契约

返回以下结果：

```yaml
report_markdown: string
output_path: string | null
stats:
  commit_count: integer
  note_file_count: integer
  note_content_count: integer
  tech_point_count: integer
  deepen_rounds: integer
warnings: string[]
```

报告至少包含：

```markdown
# 技术月报

## 已做任务

## 技术难点与解决方案

## 未来改进方向
```

## 工作流

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

### 1. 获取 GitHub commits

- 只读取 `time_range` 内的 commits；
- 保留 SHA、提交信息、日期和作者信息；
- 私有仓库必须使用用户提供的授权配置；
- API 失败时记录 warning，不因单个请求失败而终止整个流程。

### 2. 获取 commit diff

第一轮优先获取高信息量 commits 的 diff：

- 跳过 merge commit；
- 跳过明显低信息量提交，例如纯配置、图片、结果记录或简单 README 更新；
- 每个 commit 最多读取 `max_files_per_commit` 个文件；
- 每个文件最多保留 `max_chars_per_file_patch` 个字符；
- 保持原始 commit 顺序；
- 单个 commit 失败时保留 commit 元数据并继续。

### 3. 扫描本地笔记

本地笔记目录应是 Git 仓库：

- 使用 `git ls-files` 获取受跟踪文件；
- 使用 `git log` 判断文件是否在时间范围内发生过提交；
- 不使用文件 mtime 作为主要依据；
- Git 命令应使用 `-c core.quotepath=false`，避免中文路径被转义；
- 只处理支持的文本笔记格式；
- 单个文件读取失败时记录 warning 并跳过。

### 4. 双源分析

将笔记和 GitHub 数据汇合后交给分析模型：

- 笔记优先，commit 和 diff 作为补充；
- 不凭空补充用户没有提供的事实；
- 每个技术点至少提炼：`topic`、`difficulty`、`solution`、`source`；
- 结构化输出失败时，先清理 Markdown 代码块标记，再尝试解析；
- 两类数据都为空时跳过 LLM 分析并返回空结果。

### 5. 质量关卡与条件边

分析结果满足以下条件才算充分：

1. 技术点非空；
2. 技术点数量不少于 `min_tech_points`；
3. 每个技术点都有非空的主题、难点和解决方案；
4. 至少存在可引用的原始数据。

路由规则：

```python
if deepen_rounds >= max_fetch_rounds:
    return "sufficient"
if not has_more_data_to_explore:
    return "sufficient"
if is_analysis_sufficient(analysis_notes):
    return "sufficient"
return "deepen"
```

路由函数只读状态，不修改状态。状态变化必须由节点返回。

### 6. 深挖循环

当结果不充分且仍有可用数据时：

- 补充第一轮跳过的低信息量 commit；
- 适当扩大 diff 截断窗口；
- 重新分析技术点；
- 每一轮必须产生新增输入；
- 达到最大轮数后强制进入报告生成，避免死循环。

### 7. 生成和保存报告

报告生成必须：

- 使用固定的三段式结构；
- 区分主线实验、Bug 修复、预研调研和评估建设等任务；
- 尽量保留产出物、指标和风险；
- 对证据不足的内容使用谨慎措辞；
- 保存到用户指定的 `output_path`，否则使用 `docs/report_YYYY-MM-DD.md`。

## 推荐状态模型

```python
class ReportSkillState(TypedDict):
    time_range: tuple[str, str]
    repo_name: str
    github_username: str
    notes_dir: str

    github_commits: list
    note_files: list
    note_contents: list
    analysis_notes: list

    deepen_rounds: int
    final_report_md: str
    output_path: str | None
    warnings: list[str]
    token_usage: dict
```

## LangGraph 实现建议

节点职责保持单一：

```text
fetch_github_commits      只负责获取 commit 元数据
fetch_commit_diffs        只负责补充 diff
scan_notes_in_time_range  只负责扫描笔记元数据
parse_note_files          只负责读取笔记正文
analyze_tech_points       只负责双源分析
route_after_analysis      只负责返回路由 key
generate_report           只负责生成 Markdown
save_report               只负责写文件
```

图编排建议：

- 从 `START` 分叉为 GitHub 链路和笔记链路；
- 在 `analyze_tech_points` 处汇合；
- 使用 `add_conditional_edges` 实现质量关卡；
- 循环必须包含显式计数器和终止条件；
- I/O 密集型 diff 获取可使用有界线程池；
- Token 预算应在拼接 prompt 前后都进行检查。

## 错误处理原则

- fail-safe：单条数据失败不阻塞其他数据；
- 不隐藏数据源不可用的问题；
- warnings 必须随结果返回；
- 不因报告不完整而编造内容；
- 所有循环都必须可证明终止。

## 质量检查清单

在返回结果前确认：

- [ ] 时间范围明确且使用了具体日期；
- [ ] 报告三大章节齐全；
- [ ] 关键结论可以追溯到 commit 或笔记；
- [ ] 没有把推测写成事实；
- [ ] 深挖轮数没有超过上限；
- [ ] Token 或字符预算没有失控；
- [ ] warnings 已完整返回；
- [ ] 输出文件确实写入成功，或明确说明无法保存。
