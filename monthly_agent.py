"""
GitHub活动 + 学习记录自动月报生成 Agent —— Phase 1 骨架（含commit diff增强）

Phase 1 目标：
    fetch_github_commits -> fetch_commit_diffs -> analyze_tech_points -> generate_report -> save_report
"""

import os
import json
import requests
from datetime import date, datetime
from typing import TypedDict, List, Optional

from langgraph.graph import StateGraph, END, START
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv
from pydantic import SecretStr

load_dotenv()

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_API_BASE = "https://api.github.com"
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL")
DEEPSEEK_URL = os.environ.get("DEEPSEEK_URL")

# diff截断参数（2026-08-21 实验确认：500字符/文件会切断关键说明，调至1200）
MAX_FILES_PER_COMMIT = 3
MAX_CHARS_PER_FILE_PATCH = 1200

# 低信息量commit message黑名单，命中的不拉diff，省调用次数和token
LOW_INFO_MESSAGES = {
    "结果记录更新", "gitignore", "readme", "images", "docs", "config", "init",
}
# merge commit的diff通常是"这次合并累积带入的所有文件变化"，
# 跟被合并进来的其他独立commit的diff高度重复（2026-08-21实测确认），一律跳过
LOW_INFO_PREFIXES = ("merge",)


def get_llm() -> ChatOpenAI:
    if not DEEPSEEK_API_KEY:
        raise ValueError("DEEPSEEK_API_KEY 环境变量未配置，请检查 .env 文件")

    return ChatOpenAI(
        model=str(DEEPSEEK_MODEL),
        api_key=SecretStr(DEEPSEEK_API_KEY),
        base_url=str(DEEPSEEK_URL),
        temperature=0.3,
    )


# ---------------------------------------------------------------------------
# 1. State Schema
# ---------------------------------------------------------------------------

class CommitInfo(TypedDict):
    sha: str
    message: str
    date: str
    diff_summary: Optional[str]  # 只有非低信息量commit才会填充


class TechPoint(TypedDict):
    topic: str
    difficulty: str
    solution: str
    source: str


class MonthlyReportState(TypedDict):
    time_range: tuple
    repo_name: str
    github_username: str

    github_commits: List[CommitInfo]
    analysis_notes: List[TechPoint]

    final_report_md: str
    output_path: str


# ---------------------------------------------------------------------------
# 2. 节点函数
# ---------------------------------------------------------------------------

def _fetch_commits_from_github(
    owner: str, repo: str, author: str, since: date, until: date
) -> List[CommitInfo]:
    if not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN 未设置，检查 .env 文件是否正确加载")

    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/commits"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    params = {
        "author": author,
        "since": datetime.combine(since, datetime.min.time()).isoformat() + "Z",
        "until": datetime.combine(until, datetime.max.time()).isoformat() + "Z",
        "per_page": 100,
    }

    response = requests.get(url, headers=headers, params=params)
    if response.status_code != 200:
        raise RuntimeError(
            f"GitHub API请求失败：status={response.status_code}, body={response.text}"
        )

    commits: List[CommitInfo] = []
    for item in response.json():
        commits.append(
            CommitInfo(
                sha=item["sha"],
                message=item["commit"]["message"],
                date=item["commit"]["author"]["date"],
                diff_summary=None,
            )
        )
    return commits


def fetch_github_commits(state: MonthlyReportState) -> dict:
    since, until = state["time_range"]
    commits = _fetch_commits_from_github(
        owner=state["github_username"],
        repo=state["repo_name"],
        author=state["github_username"],
        since=since,
        until=until,
    )
    return {"github_commits": commits}


def _is_low_info_message(message: str) -> bool:
    first_line = message.strip().splitlines()[0].strip().lower()
    if first_line in LOW_INFO_MESSAGES:
        return True
    if first_line.startswith(LOW_INFO_PREFIXES):
        return True
    return False


def _fetch_commit_diff(owner: str, repo: str, sha: str) -> str:
    """
    2026-08-21 实验验证过：能挖出commit message看不到的技术细节
    （例：FACT_CHECK_CASES那条diff暴露了"RewardAcc上涨≠质量变好"的关键判断）。
    截断参数已按实验结果从500字符/文件调整为1200字符/文件。
    """
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/commits/{sha}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        raise RuntimeError(
            f"获取commit详情失败：status={response.status_code}, body={response.text}"
        )

    data = response.json()
    files = data.get("files", [])[:MAX_FILES_PER_COMMIT]

    diff_parts = []
    for f in files:
        filename = f.get("filename", "unknown")
        patch = f.get("patch", "")
        if not patch:
            diff_parts.append(f"[{filename}] (无文本diff，可能是二进制文件或改动过大)")
            continue
        truncated = patch[:MAX_CHARS_PER_FILE_PATCH]
        diff_parts.append(f"[{filename}]\n{truncated}")

    return "\n\n".join(diff_parts) if diff_parts else ""


def fetch_commit_diffs(state: MonthlyReportState) -> dict:
    """
    对非低信息量commit拉diff，填充diff_summary字段。
    不mutate传入的commit list，构造新列表返回。
    """
    owner = state["github_username"]
    repo = state["repo_name"]

    updated_commits: List[CommitInfo] = []
    for c in state["github_commits"]:
        if _is_low_info_message(c["message"]):
            updated_commits.append(c)  # 保持diff_summary=None
            continue

        diff_text = _fetch_commit_diff(owner, repo, c["sha"])
        updated_commits.append(
            CommitInfo(
                sha=c["sha"],
                message=c["message"],
                date=c["date"],
                diff_summary=diff_text or None,
            )
        )

    return {"github_commits": updated_commits}


ANALYSIS_PROMPT = """你是一位帮助开发者复盘技术成长的助手。以下是本月的GitHub提交记录，
部分commit附带了改动的代码diff摘要（用于补充commit message信息不足的问题）：

{commits_text}

请从中提炼出本月遇到的技术难点，输出严格的JSON数组，每个元素包含以下字段：
- topic: 技术主题（简短，几个字）
- difficulty: 遇到的具体困难描述
- solution: 如何解决/尝试了什么方法

只返回JSON数组本身，不要包含任何其他文字、解释或Markdown代码块标记。
如果commit信息不足以提炼出有意义的技术难点，返回空数组 []。
"""


def _format_commits_for_prompt(commits: List[CommitInfo]) -> str:
    parts = []
    for c in commits:
        first_line = c["message"].splitlines()[0]
        part = f"- commit: {first_line}"
        if c.get("diff_summary"):
            part += f"\n  改动详情:\n{c['diff_summary']}"
        parts.append(part)
    return "\n\n".join(parts)


def analyze_tech_points(state: MonthlyReportState) -> dict:
    commits = state["github_commits"]
    if not commits:
        return {"analysis_notes": []}

    commits_text = _format_commits_for_prompt(commits)
    prompt = ANALYSIS_PROMPT.format(commits_text=commits_text)

    model = get_llm()
    response = model.invoke(prompt)
    raw_text = response.content.strip() # type:ignore

    if raw_text.startswith("```"):
        lines = raw_text.strip("`").split("\n", 1)
        raw_text = lines[1] if len(lines) > 1 and lines[0].strip().lower() == "json" else raw_text

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"LLM返回内容不是合法JSON，原始内容：\n{raw_text}") from e

    tech_points: List[TechPoint] = [
        TechPoint(
            topic=item["topic"],
            difficulty=item["difficulty"],
            solution=item["solution"],
            source="commit",
        )
        for item in parsed
    ]
    return {"analysis_notes": tech_points}


REPORT_PROMPT = """请根据以下本月技术难点分析，生成一份结构化月报，使用Markdown格式，包含三个部分：
## 已做任务
## 技术难点与解决方案
## 未来改进方向

技术难点数据（JSON）：
{tech_points_json}

直接输出Markdown正文，不要额外解释或代码块标记。
"""


def generate_report(state: MonthlyReportState) -> dict:
    tech_points = state["analysis_notes"]
    tech_points_json = json.dumps(tech_points, ensure_ascii=False, indent=2)

    model = get_llm()
    prompt = REPORT_PROMPT.format(tech_points_json=tech_points_json)
    response = model.invoke(prompt)

    return {"final_report_md": response.content}


def save_report(state: MonthlyReportState) -> dict:
    output_path = state.get("output_path") or f"report_{date.today().isoformat()}.md"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(state["final_report_md"])
    return {"output_path": output_path}


# ---------------------------------------------------------------------------
# 3. 图结构
# ---------------------------------------------------------------------------

def build_graph():
    graph = StateGraph(state_schema=MonthlyReportState)

    graph.add_node("fetch_github_commits", fetch_github_commits)
    graph.add_node("fetch_commit_diffs", fetch_commit_diffs)
    graph.add_node("analyze_tech_points", analyze_tech_points)
    graph.add_node("generate_report", generate_report)
    graph.add_node("save_report", save_report)

    graph.add_edge(START, "fetch_github_commits")
    graph.add_edge("fetch_github_commits", "fetch_commit_diffs")
    graph.add_edge("fetch_commit_diffs", "analyze_tech_points")
    graph.add_edge("analyze_tech_points", "generate_report")
    graph.add_edge("generate_report", "save_report")
    graph.add_edge("save_report", END)

    return graph.compile()


# ---------------------------------------------------------------------------
# 4. 本地手动触发入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = build_graph()

    initial_state: MonthlyReportState = {
        "time_range": (date(2026, 8, 1), date(2026, 8, 19)),
        "repo_name": "train_llm",
        "github_username": "WenEnguang",
        "github_commits": [],
        "analysis_notes": [],
        "final_report_md": "",
        "output_path": "./docs",
    }

    result = app.invoke(initial_state)
    print("生成完成，报告路径：", result["output_path"])