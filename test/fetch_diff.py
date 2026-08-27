"""
独立脚本：测试拉取commit的diff内容，评估相比纯commit message能不能挖出更多技术细节。
 
设计取舍（先跑通再调）：
    - 每个commit只取改动最大的前3个文件（按文件在API返回中的顺序，GitHub本身
      通常按改动量排序，Phase 1先不额外排序）
    - 每个文件的patch截断到500字符以内
    - 目的：控制token开销，同时验证diff信息是否真的比commit message更有价值
 
跑通标准：
    对比同一条commit的message和diff内容，人工判断diff是否提供了
    message里看不出来的技术细节（比如具体改了什么逻辑、用了什么方法）。
"""
import os
import requests
from datetime import date
from dotenv import load_dotenv
 
load_dotenv()
 
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_API_BASE = "https://api.github.com"
 
MAX_FILES_PER_COMMIT = 3
MAX_CHARS_PER_FILE_PATCH = 500

def fetch_commit_diff(owner: str, repo: str, sha: str) -> str:
    """
    调 GET /repos/{owner}/{repo}/commits/{sha}，拿到该commit的文件改动详情。
    返回值是拼接后的、截断过的diff文本摘要。
    """
    if not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN 未设置")
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
        patch = f.get("patch", "")  # 二进制文件/超大文件可能没有patch字段
        if not patch:
            diff_parts.append(f"[{filename}] (无文本diff，可能是二进制文件或改动过大)")
            continue
        truncated = patch[:MAX_CHARS_PER_FILE_PATCH]
        diff_parts.append(f"[{filename}]\n{truncated}")
 
    return "\n\n".join(diff_parts) if diff_parts else "(无文件改动详情)"

if __name__ == "__main__":
    OWNER = "WenEnguang"
    REPO = "train_llm"
 
    TEST_SHAS = [
        "bfe809beae7c56e933d8b1b638dcc5b163beebaf",
        "52b33dd5c39c867edacda50e9d1341e9277078e7",
        "d27edcbc98f709edd6079f9e9083536de24d1538"
    ]
 
    for sha in TEST_SHAS:
        print(f"\n{'='*60}")
        print(f"commit: {sha}")
        print(f"{'='*60}")
        diff_text = fetch_commit_diff(OWNER, REPO, sha)
        print(diff_text)
        print(f"\n[本次diff文本长度: {len(diff_text)} 字符]")