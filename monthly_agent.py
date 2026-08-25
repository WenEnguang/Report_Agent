"""
GitHub活动 + 本地笔记学习记录 → 自动月报生成 Agent

数据流（两条链路并行，最后汇合到 analyze_tech_points）：
    # 链路1：GitHub commit（远程）
    fetch_github_commits -> fetch_commit_diffs ─────────┐
                                                         ├─> analyze_tech_points -> generate_report -> save_report
    # 链路2：本地 Markdown 笔记（本地独立 git 仓库）       ─┘
    scan_notes_in_time_range -> parse_md_files ─────────┘

核心设计决策（面试要能讲清楚"为什么"）：
    - 笔记链路用 git log 判断"本月是否动过"，而不是文件 mtime——
      Obsidian 会自动保存，mtime 是磁盘写入噪音，git 时间戳才代表真实内容产出
    - 用 git ls-files 拿文件列表，而不是 os.walk——
      路径与 commit 历史严格一致（避 Windows 大小写坑），且天然排除未跟踪文件
    - scan（拿元数据）与 parse（读内容）拆成两个节点，职责单一
"""

import os
import json
import subprocess
import requests
from pathlib import Path
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

# 单篇笔记拼进 prompt 时的字符上限。
# 实测 Obsidian 笔记动辄几万字（如 AI学习.md 达 41KB），全量塞进 prompt 会
# token 爆炸、稀释重点、甚至超上下文窗口，所以截断到固定长度（与上面的
# commit diff 截断同一思路）。后续可升级为分块 / 先摘要再分析。
MAX_CHARS_PER_NOTE = 4000

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
# 1. State Schema（LangGraph 节点间传数据的唯一通道）
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
    source: str  # 技术点来源：commit / note / commit+note


class NoteFileInfo(TypedDict):
    """扫描阶段产物：笔记文件的元数据（scan 后不再变化）。"""
    path: str          # 相对笔记仓库的路径（git ls-files 输出）
    last_commit: str   # 最后 commit 时间（原始字符串，如 "2026-08-24 14:38:59 +0800"）
    ext: str           # 扩展名（.md / .pdf / .docx），供 parse 阶段分流


class NoteContent(TypedDict):
    """解析阶段产物：笔记的完整正文。"""
    path: str
    content: str


class MonthlyReportState(TypedDict):
    time_range: tuple   # (since, until) 两个 date 对象
    repo_name: str      # GitHub 仓库名（只填仓库名，如 "train_llm"，不填完整URL）
    notes_dir: str      # 本地笔记目录（独立 git 仓库的绝对路径）
    github_username: str

    github_commits: List[CommitInfo]
    analysis_notes: List[TechPoint]
    note_files: List[NoteFileInfo]      # 扫描出的本月笔记文件（元数据）
    note_contents: List[NoteContent]    # 解析出的本月笔记正文

    final_report_md: str
    output_path: str


# ---------------------------------------------------------------------------
# 2. 节点函数 —— 链路2：本地笔记
# ---------------------------------------------------------------------------

def _run_git_command(cwd: str, args: List[str]) -> str:
    """
    在指定 git 仓库目录下执行 git 命令，返回 stdout。

    两个关键点：
    1. subprocess 用【列表传参】而不是拼字符串——文件名带空格/中文也安全；
       拼字符串等于把解析权交给 shell，等于留了注入漏洞（面试常考）。
    2. `-c core.quotepath=false` 是关键：默认情况下 git 会把非 ASCII 文件名
       转义成八进制（如 "AI学习.md" 显示成 "\345\255\246..."），导致后面
       路径匹配不到、文件静默丢失。这个开关让 git 直接输出原始 UTF-8 路径。
    """
    result = subprocess.run(
        ["git", "-c", "core.quotepath=false"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",   # 兜底：万一有无法解码的字节，用替换符而非让进程崩
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git 命令执行失败：git {' '.join(args)}\n"
            f"stderr: {result.stderr}"
        )
    return result.stdout


def _scan_notes_directory(notes_dir: str) -> List[NoteFileInfo]:
    """
    用 git ls-files 获取仓库中所有被追踪的笔记文件，并取每个文件最后 commit 时间。

    为什么用 git ls-files 而不是 os.walk：
        - 只返回 git 管理的文件，天然排除未追踪的临时文件
        - 路径与 commit 历史严格一致（避 Windows 大小写坑）
        - 自动跳过 .gitignore 排除的文件
    """
    # 1. 拿所有被追踪的笔记文件（pathspec 通配符）
    output = _run_git_command(
        notes_dir,
        ["ls-files", "--", "*.md", "*.markdown", "*.txt"],
    )
    all_files = [line.strip() for line in output.strip().split("\n") if line.strip()]

    results: List[NoteFileInfo] = []
    for file_path in all_files:
        # 2. 每个文件取最后一次 commit 的作者时间（-1 只要最近一条）
        log_output = _run_git_command(
            notes_dir,
            ["log", "-1", "--format=%ai", "--", file_path],
        )
        last_commit_str = log_output.strip()

        if not last_commit_str:
            # 理论上 ls-files 列出的文件一定有 commit 历史，这里只是 fail-safe 兜底
            continue

        ext = Path(file_path).suffix.lower()
        results.append(
            NoteFileInfo(
                path=file_path,
                last_commit=last_commit_str,
                ext=ext,
            )
        )

    return results


def scan_notes_in_time_range(state: MonthlyReportState) -> dict:
    """
    节点1（笔记链路）：扫描笔记仓库，只保留"本月内 commit 过"的文件。

    核心逻辑：
        - git ls-files 拿全部追踪文件
        - git log -1 --format=%ai 拿每个文件最后 commit 时间
        - 按 state['time_range'] 过滤，落在 [since, until] 内才保留
    """
    since, until = state["time_range"]
    notes_dir = state["notes_dir"]

    all_notes = _scan_notes_directory(notes_dir)

    monthly_notes: List[NoteFileInfo] = []
    for note in all_notes:
        # commit 日期格式："2026-08-24 14:38:59 +0800"
        # 前 19 个字符是 "YYYY-MM-DD HH:MM:SS"，转成 datetime 再取 .date() 比较
        commit_dt = datetime.strptime(note["last_commit"][:19], "%Y-%m-%d %H:%M:%S")

        if since <= commit_dt.date() <= until:
            monthly_notes.append(note)

    return {"note_files": monthly_notes}


def parse_md_files(state: MonthlyReportState) -> dict:
    """
    节点2（笔记链路）：读取本月笔记文件的【完整正文】。

    为什么读全文而不是只取 diff 片段？
        LLM 分析"我本月学了什么"需要完整上下文。diff 片段会断章取义，
        可能让 LLM 提炼出错误的技术点。所以直接读全文，让 LLM 自己判断重点。
    """
    notes_dir = state["notes_dir"]
    contents: List[NoteContent] = []

    for note in state["note_files"]:
        # note_files 里存的是相对路径（git 输出），要拼上笔记目录才是磁盘真实路径
        full_path = Path(notes_dir) / note["path"]

        try:
            text = full_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            # 单个文件读失败不能中断整条链路，记录并跳过（fail-safe）
            print(f"[警告] 读取笔记失败，已跳过: {note['path']}（{e}）")
            continue

        contents.append(NoteContent(path=note["path"], content=text))

    return {"note_contents": contents}


# ---------------------------------------------------------------------------
# 2. 节点函数 —— 链路1：GitHub commit
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


# ---------------------------------------------------------------------------
# 2. 节点函数 —— 汇合节点：LLM 分析
# ---------------------------------------------------------------------------

ANALYSIS_PROMPT = """你是一位帮助开发者复盘技术成长的助手。以下是本月收集到的学习材料：

一、本地笔记（优先参考——反映你本月学习/思考的原始记录）：

{notes_text}

二、GitHub提交记录（作为补充——部分commit附带了代码diff摘要）：

{commits_text}

请从中提炼出本月遇到的技术难点，输出严格的JSON数组，每个元素包含以下字段：
- topic: 技术主题（简短，几个字）
- difficulty: 遇到的具体困难描述
- solution: 如何解决/尝试了什么方法

只返回JSON数组本身，不要包含任何其他文字、解释或Markdown代码块标记。
如果材料不足以提炼出有意义的技术难点，返回空数组 []。
"""


def _format_commits_for_prompt(commits: List[CommitInfo]) -> str:
    """把 commit 列表拼成 prompt 可用的文本。"""
    parts = []
    for c in commits:
        first_line = c["message"].splitlines()[0]
        part = f"- commit: {first_line}"
        if c.get("diff_summary"):
            part += f"\n  改动详情:\n{c['diff_summary']}"
        parts.append(part)
    return "\n\n".join(parts)


def _format_notes_for_prompt(notes: List[NoteContent]) -> str:
    """把笔记列表拼成 prompt 可用的文本（与 _format_commits_for_prompt 对称）。"""
    parts = []
    for n in notes:
        # path 让 LLM 知道"这是哪篇笔记"，content 是完整正文
        content = n["content"]
        # 截断到 MAX_CHARS_PER_NOTE，控制 token 成本（见顶部常量注释）
        if len(content) > MAX_CHARS_PER_NOTE:
            content = content[:MAX_CHARS_PER_NOTE] + "\n...[内容过长，已截断]"
        parts.append(f"### 笔记: {n['path']}\n{content}")
    return "\n\n".join(parts)


def analyze_tech_points(state: MonthlyReportState) -> dict:
    """
    汇合节点：两个数据源都到这，拼进同一个 prompt 让 LLM 提炼技术点。

    LLM 接入的 5 步范式（面试要能背出来）：
        1. 结构化数据 -> 文本（_format_*_for_prompt）
        2. 填 prompt 模板（prompt.format(...)）
        3. model.invoke(prompt)
        4. json.loads 解析
        5. 组装对象写回 state（return {"analysis_notes": [...]}）
    """
    commits = state["github_commits"]
    notes = state["note_contents"]

    # 两个数据源都为空时直接短路，省一次 LLM 调用（也省 token 成本）
    if not commits and not notes:
        return {"analysis_notes": []}

    commits_text = _format_commits_for_prompt(commits)
    notes_text = _format_notes_for_prompt(notes)
    prompt = ANALYSIS_PROMPT.format(commits_text=commits_text, notes_text=notes_text)

    model = get_llm()
    response = model.invoke(prompt)
    raw_text = response.content.strip()  # type:ignore

    # LLM 可能额外包一层 ```json ... ```，先剥掉再解析
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
            # 笔记和commit混在一个prompt里，LLM无法区分每个点具体来自哪，
            # 这里统一标记为两者；后续若需精确溯源，可改成两次独立调用
            source="commit+note",
        )
        for item in parsed
    ]
    return {"analysis_notes": tech_points}


# ---------------------------------------------------------------------------
# 2. 节点函数 —— 出报告
# ---------------------------------------------------------------------------

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

    # 链路1：GitHub
    graph.add_node("fetch_github_commits", fetch_github_commits)
    graph.add_node("fetch_commit_diffs", fetch_commit_diffs)
    # 链路2：本地笔记
    graph.add_node("scan_notes_in_time_range", scan_notes_in_time_range)
    graph.add_node("parse_md_files", parse_md_files)
    # 汇合 + 出报告
    graph.add_node("analyze_tech_points", analyze_tech_points)
    graph.add_node("generate_report", generate_report)
    graph.add_node("save_report", save_report)

    # START 分叉成两条并行链路
    graph.add_edge(START, "fetch_github_commits")
    graph.add_edge(START, "scan_notes_in_time_range")
    # 链路1 内部
    graph.add_edge("fetch_github_commits", "fetch_commit_diffs")
    # 链路2 内部
    graph.add_edge("scan_notes_in_time_range", "parse_md_files")
    # 两条链路汇合到 analyze_tech_points
    # （LangGraph 的语义：一个节点只有等它所有上游边都完成后才会执行）
    graph.add_edge("fetch_commit_diffs", "analyze_tech_points")
    graph.add_edge("parse_md_files", "analyze_tech_points")
    # 出报告
    graph.add_edge("analyze_tech_points", "generate_report")
    graph.add_edge("generate_report", "save_report")
    graph.add_edge("save_report", END)

    return graph.compile()


# ---------------------------------------------------------------------------
# 4. 本地手动触发入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # 入口只做三件事：建图 -> 填初值 -> invoke。
    # 所有业务逻辑（扫描、解析、LLM 分析）都在节点函数里，不要写在这里。
    app = build_graph()

    notes_dir = os.environ.get("NOTES_DIR")
    if not notes_dir:
        raise RuntimeError("NOTES_DIR 环境变量未配置，请在 .env 里设置笔记目录路径（如 E:/Markdown）")

    initial_state: MonthlyReportState = {
        "time_range": (date(2026, 8, 1), date(2026, 8, 25)),
        "repo_name": "train_llm",        # GitHub 仓库名（只填名字，不填完整 URL）
        "notes_dir": notes_dir,          # 本地笔记目录的绝对路径
        "github_username": "WenEnguang", # GitHub 用户名（author 参数按它过滤）
        "github_commits": [],
        "analysis_notes": [],
        "note_files": [],
        "note_contents": [],
        "final_report_md": "",
        "output_path": f"docs/report_{date.today().isoformat()}.md",
    }

    result = app.invoke(initial_state)

    print("\n===== 月报生成完成 =====")
    print(f"报告路径:     {result['output_path']}")
    print(f"本月笔记数:   {len(result['note_files'])}")
    print(f"GitHub commits: {len(result['github_commits'])}")
    print(f"提炼技术点:   {len(result['analysis_notes'])}")
