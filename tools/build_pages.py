"""按仓库生成分析目录页，仅发布 Git 跟踪的静态产物。"""
from collections import defaultdict
from html import escape
from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
ASSETS = {".html", ".css", ".js", ".svg", ".png", ".jpg", ".jpeg", ".gif",
          ".webp", ".avif", ".ico", ".woff", ".woff2", ".ttf", ".otf"}
REPOSITORY_URL = "https://github.com/intellif-aied/repo-reading"


class Title(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "title" and not self.parts:
            self.active = True

    def handle_endtag(self, tag):
        if tag == "title":
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)


def index_html(title, description, entries, back=False):
    items = "\n".join(
        f'<li><a href="{escape(url, quote=True)}">{escape(label)}</a>'
        f'<small>{escape(detail)}</small></li>'
        for url, label, detail in entries
    )
    navigation = '<nav><a href="../">返回仓库列表</a></nav>' if back else ''
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} — 源码阅读</title>
<style>
body{{max-width:880px;margin:64px auto;padding:0 24px;background:#f5f5f5;color:#2d3142;font:16px/1.6 system-ui,sans-serif}}
h1{{font:400 36px/1.4 Georgia,serif}}a{{color:#2e5aa8;text-underline-offset:4px}}
ul{{list-style:none;padding:0}}li{{padding:24px 0;border-top:1px solid #bfc0c0}}
small{{display:block;color:#4f5d75;margin-top:8px;overflow-wrap:anywhere}}footer{{margin-top:48px;color:#4f5d75}}
</style></head><body><main>{navigation}<h1>{escape(title)}</h1>
<p>{escape(description)}</p><ul>{items}</ul>
<footer><a href="{REPOSITORY_URL}">查看分析仓库</a></footer>
</main></body></html>
'''


def build_site(root):
    root = Path(root).resolve()
    site = root / "_site"
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=root)
    paths = sorted(Path(p.decode()) for p in tracked.split(b"\0") if p)
    tracked_paths = set(paths)
    # _site 是专用且被忽略的生成目录。
    if site.exists():
        shutil.rmtree(site)
    site.mkdir()
    groups = defaultdict(list)
    for relative in paths:
        if relative.parts[0].startswith(".") or relative.parts[0] == "_site":
            continue
        source = root / relative
        if source.suffix.lower() not in ASSETS or not source.is_file():
            continue
        if source.is_symlink() or root not in source.resolve().parents:
            raise ValueError(f"Static asset must be a regular repository file: {relative}")
        target = site / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if source.suffix.lower() == ".html" and relative.parts[0].endswith("-analysis"):
            title = Title()
            title.feed(source.read_text())
            groups[relative.parts[0]].append(
                (relative, "".join(title.parts).strip() or relative.stem)
            )
    if not groups:
        raise ValueError("No tracked analysis HTML pages found")

    repositories = []
    for directory, pages in sorted(groups.items()):
        repo = directory[:-len("-analysis")]
        overview = Path(directory) / "index.html"
        entries = [
            (quote(relative.relative_to(directory).as_posix(), safe="/"), title,
             relative.relative_to(directory).as_posix())
            for relative, title in pages if relative != overview
        ]
        walkthrough = Path(directory) / "walkthrough.md"
        if walkthrough in tracked_paths and (root / walkthrough).is_file():
            entries.append((f"{REPOSITORY_URL}/blob/master/{quote(walkthrough.as_posix(), safe='/')}",
                            "线性源码讲解", "在 GitHub 阅读完整讲解与源码摘录"))
        # 手工维护的仓库主页优先；其余仓库自动获得汇总页。
        if not (site / overview).exists():
            (site / overview).write_text(index_html(
                f"{repo} 源码分析", "选择一份分析继续阅读。", entries, back=True
            ))
        repositories.append((quote(directory, safe="") + "/", repo, "查看该仓库的分析、图表与源码讲解"))

    (site / "index.html").write_text(index_html(
        "源码阅读", "每个仓库一个入口，相关分析集中在仓库目录页。", repositories
    ))
    (site / ".nojekyll").touch()
    print(f"构建完成：{len(groups)} 个仓库入口，{sum(len(p) for p in groups.values())} 张分析页面。")


if __name__ == "__main__":
    build_site(ROOT)
