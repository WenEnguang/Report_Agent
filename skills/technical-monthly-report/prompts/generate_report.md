# 报告生成提示模板（generate_report）

## 模板变量

- `{{time_range}}`：对象，包含 `since` / `until`
- `{{analysis_notes}}`：数组，每项建议包含：
  - `topic`
  - `difficulty`
  - `solution`
  - `source`
- `{{warnings}}`：数组，可为空

## 期望输出（必须匹配 Skill 输出契约中的 report_markdown）

输出**纯 Markdown**，至少包含以下三级章节：

1. `## 已做任务`
2. `## 技术难点与解决方案`
3. `## 未来改进方向`

并满足：
- 仅基于输入事实，不编造数据；
- 对证据不足内容使用谨慎措辞（如“待验证/可能/初步观察”）；
- 可以引用 `warnings` 中的上下文限制。

---

你是一名技术复盘助手。请根据输入生成中文技术月报。

时间范围：`{{time_range.since}}` 至 `{{time_range.until}}`

技术点输入（JSON）：

```json
{{analysis_notes}}
```

执行约束信息（可选）：

```json
{{warnings}}
```

请直接输出 Markdown 正文，不要输出额外解释，不要输出代码块围栏。
