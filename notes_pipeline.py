"""
GitHub活动 + 学习记录自动月报生成 Agent —— Phase 1 骨架（含commit diff增强）

Phase 1 目标：
    fetch_github_commits -> fetch_commit_diffs -> analyze_tech_points -> generate_report -> save_report
"""

import os
import json
import requests
import subprocess
from pathlib import Path
from datetime import date, datetime
from typing import TypedDict, List, Optional,Dict

from langgraph.graph import StateGraph, END, START
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv
from pydantic import SecretStr
from rich import print as rprint

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

class NoteFileInfo(TypedDict):  # 负责扫描笔记文件信息的数据结构
    path: str
    last_commit: str
    ext: str

class NoteContent(TypedDict):   # 负责数据内容的数据结构
    path: str
    content:str

class MonthlyReportState(TypedDict):
    time_range: tuple
    repo_name: str
    repo_dir: str
    github_username: str

    github_commits: List[CommitInfo]
    analysis_notes: List[TechPoint]
    note_files: List[NoteFileInfo]    # 新增：扫描本地提交的内容
    notes_contents: List[NoteContent]    #新增： 分析本地笔记内容

    final_report_md: str
    output_path: str


# ---------------------------------------------------------------------------
# 2. 节点函数
# ---------------------------------------------------------------------------
# 支持的笔记文件扩展名
SUPPORTED_EXTS = {".md", ".markdown", ".ipynb", ".txt", ".rst", ".org"}

# 要跳过的目录（虚拟环境、缓存、隐藏目录等）
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".obsidian", ".trash", ".idea"}

# ++++++++++++++++++++++ 读取本地的目录 +++++++++++++++++++++++++++++++
def _run_git_command(repo_dir: str, args: List[str]) -> str:
    """在指定 Git 仓库目录下执行 git 命令，返回 stdout"""
    result = subprocess.run(
        ["git"] + args,
        cwd=repo_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git 命令执行失败：git {' '.join(args)}\n"
            f"stderr: {result.stderr}"
        )
    return result.stdout

def _scan_notes_directory(repo_dir: str) -> List[NoteFileInfo]:
    """
    用 git ls-files 获取仓库中所有被 Git 追踪的 .md 文件。
    
    相比 os.walk 的优势：
        - 只返回 Git 管理的文件，不会扫到未追踪的临时文件
        - 跳过 .gitignore 里排除的文件
        - 路径格式和 git diff 输出一致，后续处理统一
    
    返回格式：
        [
            {"path": "Coding.md", "last_commit": "2026-08-21 18:06:23", "ext": ".md"},
            ...
        ]
    """
    # 1. 用 git ls-files 获取所有 .md 文件
    output = _run_git_command(
        repo_dir,
        ["ls-files", "--", "*.md", "*.markdown", "*.txt"],
    )

    all_files = [line.strip() for line in output.strip().split("\n") if line.strip()]

    results: List[NoteFileInfo] = []
    for file_path in all_files:
        # 2. 对每个文件，用 git log 获取最后一次 commit 的时间
        log_output = _run_git_command(
            repo_dir,
            ["log", "-1", "--format=%ai", "--", file_path],
        )
        last_commit_str = log_output.strip()
        
        if not last_commit_str:
            continue  # 没有 commit 记录的文件跳过
        
        ext = Path(file_path).suffix.lower()
        
        results.append(
            NoteFileInfo(
                path=file_path,
                last_commit=last_commit_str,
                ext=ext,
            )
        )
    
    return results

def scan_notes_in_time_range(state:MonthlyReportState) -> dict:
    """
    扫描Git仓库中的笔记文件，只返回本月内有过的commit的文件

    核心逻辑：
        - 用 git ls-files拿到所有的追踪的.md文件
        - 用 git log -1 --format=%ai 拿到每个文件的最后commit时间
        - 按state['time_range']过滤，只保留本月内修改过的文件
        - 输出写回 {"note_files": [...]}
    """
    since,until = state['time_range']
    repo_dir = state['repo_dir']

    all_notes = _scan_notes_directory(repo_dir=repo_dir)

    # 按照时间进行过滤
    monthly_notes:List[NoteFileInfo] = []
    for note in all_notes:
        # commit date 的数据格式：2026-08-24 14:38:59 +0800
        # 保留前19个字符
        commit_dt = datetime.strptime(note["last_commit"][:19], "%Y-%m-%d %H:%M:%S")

        if since <= commit_dt.date() <= until:
            monthly_notes.append(note)

    return {
        'note_files':monthly_notes
    }


def _find_baseline_commit(repo_dir: str, since: datetime) -> Optional[str]:
    """
    找到月初之前最后一个 commit 的 hash，作为 diff 的 baseline。

    例：如果 8月1日 之前最后一个 commit 是 7月28日 的，
    那 baseline 就是那个 commit，diff 出来的就是 7月28日之后的所有变更。
    """
    output = _run_git_command(
        repo_dir,
        [
            "log",
            f"--before={since.isoformat()}",
            "--pretty=format:%H",
            "-n", "1",  # 只要最近的一个
        ],
    )
    hash_str = output.strip()
    return hash_str if hash_str else None


def get_monthly_diff(state: MonthlyReportState) -> Dict:
    """
    节点2：获取本月笔记文件的 diff 变更
    """
    since, until = state["time_range"]
    repo_dir = state["repo_dir"]
    note_files = state.get("note_files", [])

    if not note_files:
        rprint("[yellow]⚠️ 没有笔记文件需要 diff[/yellow]")
        return {"notes_contents": []}

    # 获取 baseline commit
    baseline = _find_baseline_commit(repo_dir, since)
    if not baseline:
        baseline = _run_git_command(repo_dir, ["rev-list", "--max-parents=0", "HEAD"]).strip()

    end_output = _run_git_command(
        repo_dir,
        ["log", f"--before={until.isoformat()}", "--pretty=format:%H", "-n", "1"],
    )
    end_point = end_output.strip() or "HEAD"

    rprint(f"[bold]Diff 范围:[/bold] {baseline[:8]}..{end_point[:8]}")

    contents: List[NoteContent] = []

    for note in note_files:
        file_path = note["path"]

        try:
            diff_args = ["diff", baseline, end_point, "--", file_path]
            diff_output = _run_git_command(repo_dir, diff_args)

            if not diff_output.strip():
                continue

            # 解析 diff，提取变更行
            added_lines = []
            deleted_lines = []

            for line in diff_output.split("\n"):
                if line.startswith("+") and not line.startswith("+++"):
                    added_lines.append(line[1:])
                elif line.startswith("-") and not line.startswith("---"):
                    deleted_lines.append(line[1:])

            if added_lines or deleted_lines:
                content = {
                    "path": file_path,
                    "added_lines": added_lines[:50],  # 限制长度
                    "deleted_lines": deleted_lines[:50],
                    "added_count": len(added_lines),
                    "deleted_count": len(deleted_lines),
                }
                contents.append(NoteContent(
                    path=file_path,
                    content=json.dumps(content, ensure_ascii=False, indent=2),
                ))
                rprint(f"[dim]   📝 {file_path}: +{len(added_lines)} -{len(deleted_lines)}[/dim]")

        except Exception as e:
            rprint(f"[red]❌ 处理文件 {file_path} 失败: {e}[/red]")
            continue

    rprint(f"[green]✅ Diff 完成: {len(contents)} 个文件有变更[/green]")
    return {"notes_contents": contents}

if __name__ == "__main__":
    """测试数据读取流程，不调用 LLM"""
    # ---------- 1. 准备测试状态 ----------
    repo_dir = os.environ.get("NOTES_DIR")
    if not repo_dir:
        rprint("目录环境变量缺失")
        raise FileExistsError()
        
    
    test_state: MonthlyReportState = {
        "time_range": (date(2026, 8, 1), date(2026, 8, 25)),
        "repo_dir": repo_dir,
        "repo_name": os.environ.get("REPO_NAME", "test-repo"),
        "github_username": os.environ.get("GITHUB_USERNAME", "test-user"),
        "github_commits": [],
        "analysis_notes": [],
        "note_files": [],
        "notes_contents": [],
        "final_report_md": "",
        "output_path": "./test_output.md",
    }
    
    rprint(f"[dim]📂 测试目录: {repo_dir}[/dim]")
    rprint(f"[dim]📅 时间范围: {test_state['time_range']}[/dim]\n")

    # ---------- 2. 测试 scan_notes_in_time_range ----------
    rprint("[bold yellow]▶️ 测试节点1: scan_notes_in_time_range[/bold yellow]")
    
    try:
        result1 = scan_notes_in_time_range(test_state)
        note_files = result1.get("note_files", [])
        
        rprint(f"[green]✅ 扫描完成: 发现 {len(note_files)} 个本月有变更的文件[/green]")
        
        if note_files:
            rprint("\n[cyan]📋 本月变更文件列表（前5个）:[/cyan]")
            for i, note in enumerate(note_files[:5]):
                rprint(f"  {i+1}. {note['path']}")
                rprint(f"     最后提交: {note['last_commit']}")
                rprint(f"     扩展名: {note['ext']}")
        else:
            rprint("[yellow]⚠️ 本月没有变更的笔记文件[/yellow]")
        
        # 更新状态用于下一步
        test_state["note_files"] = note_files
        
    except Exception as e:
        rprint(f"[red]❌ 节点1失败: {e}[/red]")
        import traceback
        traceback.print_exc()
    
    rprint("[dim]" + "-" * 50 + "[/dim]\n")

    # ---------- 3. 测试 get_monthly_diff ----------
    rprint("[bold yellow]▶️ 测试节点2: get_monthly_diff[/bold yellow]")
    
    try:
        result2 = get_monthly_diff(test_state)
        notes_contents = result2.get("notes_contents", [])
        
        rprint(f"[green]✅ Diff 完成: {len(notes_contents)} 个文件有变更[/green]")
        
        if notes_contents:
            rprint("\n[cyan]📝 Diff 摘要（前3个）:[/cyan]")
            for i, content in enumerate(notes_contents[:3]):
                rprint(f"\n  {i+1}. {content['path']}")
                try:
                    data = json.loads(content['content'])
                    rprint(f"     新增行数: {data.get('added_count', 0)}")
                    rprint(f"     删除行数: {data.get('deleted_count', 0)}")
                    if data.get('added_lines'):
                        rprint(f"     新增样例: {data['added_lines'][0][:50]}...")
                except:
                    rprint(f"     内容预览: {content['content'][:100]}...")
        else:
            rprint("[yellow]⚠️ 没有文件有 diff 变更[/yellow]")
        
    except Exception as e:
        rprint(f"[red]❌ 节点2失败: {e}[/red]")
        import traceback
        traceback.print_exc()
    
    rprint("\n[bold green]🎉 数据流水线测试完成！[/bold green]")
    rprint("[dim]💡 提示: 数据读取正常，可以引入 LLM 进行下一步测试[/dim]")