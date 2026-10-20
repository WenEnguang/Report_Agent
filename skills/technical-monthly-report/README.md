# Technical Monthly Report Skill

 从`Report_Agent` 提炼出的可复用工作流型 Skill。

## 特点

- GitHub commits + diff 与本地 Git Markdown 笔记双源输入；
- LangGraph 风格的并行分支和汇合；
- 条件边质量关卡；
- 不充分时有限次数深挖；
- Token/字符预算控制；
- Markdown 月报输出；
- 单条数据失败隔离。

## 使用方式

将 `SKILL.md` 作为 Skill 定义文件交给支持 Skill 的 Agent 或运行时，并提供：

```yaml
time_range:
  since: 2026-08-01
  until: 2026-08-31
github:
  repo: WenEnguang/Report_Agent
  username: WenEnguang
notes:
  directory: E:/Markdown
output_path: docs/report_2026-08-31.md
```

然后发出：

```text
请使用 technical-monthly-report Skill，生成指定时间范围的中文技术月报。
```

## 与 LangGraph 的关系

Skill 是对外的业务能力；LangGraph 是内部编排实现。可以将当前仓库的 `monthly_agent.py` 拆分为：

```text
state.py       State Schema
nodes/         各节点实现
routes/        条件边和质量判断
graph.py       StateGraph 构建
skill.py       对外 run() 接口
```

## 最小调用接口

```python
result = generate_monthly_report(
    time_range=("2026-08-01", "2026-08-31"),
    repo_name="WenEnguang/Report_Agent",
    github_username="WenEnguang",
    notes_dir="E:/Markdown",
)
```

## 注意事项

1. `notes.directory` 必须是本地 Git 仓库路径；
2. GitHub 私有仓库需要有效 Token；
3. 不要把 API Key 写入 Skill 文件；
4. Skill 只负责预定义的报告工作流，不负责开放式研究规划；
5. 如果需要自主决定研究方向，应由上层 Agent 调用本 Skill。
