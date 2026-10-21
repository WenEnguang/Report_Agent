# technical-monthly-report（可复用 workflow skill）

本目录是一个**自包含**的 workflow-based skill 包：只下载本目录，也能完成输入准备、调用与输出理解。

## 1) 这是什么

- 这是 **Agent Skill 定义**，不是可直接 `pip install` 的 Python 包。
- 本目录不提供 `generate_monthly_report()` 这类可直接导入调用的函数。
- 你应把本目录交给支持 Skill 的 Agent/运行时执行。

入口文档：`./SKILL.md`

## 2) 目录结构与用途

```text
technical-monthly-report/
├─ SKILL.md                      # Skill 主契约（输入/输出/终止/降级/权限）
├─ README.md                     # 使用说明（本文件）
├─ manifest.yaml                 # 清单：入口、示例、依赖能力、实现状态
├─ references/
│  └─ workflow.md                # 工作流逻辑节点与节点契约参考
├─ prompts/
│  └─ generate_report.md         # 报告生成模板（变量与输出要求）
└─ examples/
   └─ input.yaml                 # 合法 YAML 输入示例（最小+扩展）
```

## 3) 快速使用（仅下载本目录）

### 步骤 A：复制目录
将 `technical-monthly-report/` 放到你的 Skill 搜索路径下。

### 步骤 B：准备输入
参考 `examples/input.yaml`，至少提供：
- `time_range.since`
- `time_range.until`
- `github.repo`
- `github.username`
- `notes.directory`

### 步骤 C：调用 Skill（示例）

```text
请使用 technical-monthly-report skill。
输入参数使用 examples/input.yaml 中的 monthly_report_request。
输出中文技术月报，并返回 warnings/errors 与 stats。
```

## 4) 输入输出要点

- 输入契约：见 `SKILL.md` 第 3 节
- 输出契约：见 `SKILL.md` 第 4 节
- 提示模板：见 `prompts/generate_report.md`

## 5) 运行时依赖与权限

- GitHub API 读取权限（私有仓库需 token）
- 本地 notes Git 仓库读取权限（`git ls-files`、`git log`、文件读取）
- 可访问 LLM 服务

如果任一能力不可用，必须返回 `warnings` 或 `errors`，不能伪造成功。

## 6) 常见故障排查

1. **报错：notes 目录不可访问**  
   - 检查 `notes.directory` 是否为绝对路径，且目录是 Git 仓库
2. **报错：GitHub 401/403**  
   - 检查 token 是否有效且具备目标仓库读取权限
3. **输出为空或技术点过少**  
   - 扩大 `time_range`，或提高输入数据质量；必要时增大 `constraints.max_fetch_rounds`
4. **只返回部分结果（partial）**  
   - 查看 `warnings`，确认是 GitHub 还是 notes 数据源降级

## 7) 与仓库根目录实现的关系

本 Skill 可独立使用，不依赖仓库根目录文件。  
根目录 `monthly_agent.py` 仅是同类工作流的历史实现参考，不是本 Skill 的运行前置条件。
