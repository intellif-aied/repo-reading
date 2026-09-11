"""运行技能自检，并核对仓库要求的 GitHub 导航链接例外。"""
import argparse
import importlib.util
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REV = "5db13a131e10e8ee2105211f665412ebc13bd98e"
PREFIX = f"/Tencent/WeKnora/blob/{REV}/"
WALK = "/intellif-aied/repo-reading/blob/master/WeKnora-analysis/walkthrough.md"

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--skill-checker", type=Path, help="已安装技能的 scripts/self_check.py")
args = parser.parse_args()
checker = args.skill_checker
if checker is None:
    candidates = sorted((Path.home() / ".codex/plugins/cache/diagram-design").glob(
        "**/skills/diagram-design/scripts/self_check.py"))
    if len(candidates) != 1:
        parser.error("无法唯一定位技能检查器，请通过 --skill-checker 指定发现的路径")
    checker = candidates[0]
spec = importlib.util.spec_from_file_location("diagram_self_check", checker)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

walkthrough = (HERE / "walkthrough.md").read_text()
headings = re.findall(r"^## (.+)$", walkthrough, re.M)
anchors = {re.sub(r"[^\w\- ]", "", h.lower()).replace(" ", "-") for h in headings}


def link_errors(value, parent):
    url = urlparse(value)
    errors = []
    if url.netloc == "github.com" and url.path.startswith(PREFIX):
        path = ROOT / "WeKnora" / unquote(url.path[len(PREFIX):])
        if not path.is_file():
            errors.append(f"源码文件不存在：{path}")
        elif url.fragment:
            match = re.fullmatch(r"L(\d+)", url.fragment)
            if not match or not 1 <= int(match[1]) <= len(path.read_text().splitlines()):
                errors.append(f"源码行号无效：{value}")
    elif url.path == WALK and url.fragment and unquote(url.fragment) not in anchors:
        errors.append(f"章节不存在：{value}")
    elif not url.scheme and url.path and not (parent / unquote(url.path)).is_file():
        errors.append(f"本地链接不存在：{value}")
    return errors


failed = False
for name in ["architecture.html", "flow.html", "qa-flow.html"]:
    path = HERE / name
    document = module.parsed_document(path.read_text())
    accepted = []
    errors = []
    for tag, rel, value in document.references:
        issue = module.reference_error(tag, rel, value)
        url = urlparse(value)
        allowed = (url.path.startswith(PREFIX) or url.path == WALK
                   or url.path == f"/Tencent/WeKnora/tree/{REV}")
        if issue and tag == "a" and url.scheme == "https" and url.netloc == "github.com" and allowed:
            accepted.append(issue)
        if tag == "a":
            errors.extend(link_errors(value, path.parent))
    original = module.verify(path)
    remaining = list(original)
    for exception in accepted:
        remaining.remove(exception)
    remaining.extend(errors)
    print(f"{name}：原自检 {len(original)} 项；已说明导航例外 {len(accepted)} 项；其他问题 {len(remaining)} 项")
    for issue in remaining:
        print("  " + issue)
    failed |= bool(remaining)

# 摘录内容属于上游源码，不把其自身的 Markdown 当成本分析的链接。
prose = re.sub(r"^```[^\n]*\n.*?^```\s*$", "", walkthrough, flags=re.M | re.S)
errors = []
links = re.findall(r"\]\(([^)]+)\)", prose)
for value in links:
    errors.extend(link_errors(value, HERE))
print(f"walkthrough.md：{len(headings)} 节，{len(links)} 个讲解链接，{len(errors)} 个链接问题")
for issue in errors:
    print("  " + issue)
raise SystemExit(1 if failed or errors else 0)
