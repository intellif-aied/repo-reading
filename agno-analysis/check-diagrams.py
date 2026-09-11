"""调用技能原检查器，逐项记录仓库要求的 GitHub 导航链接例外。"""
import importlib.util
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

REV = "4253dedc95250afddfcea05dfc28f26a4833a117"
ROOT = Path(__file__).resolve().parent.parent
checker = Path(sys.argv[1]).resolve()
spec = importlib.util.spec_from_file_location("diagram_self_check", checker)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
failed = False
walkthrough = (ROOT / "agno-analysis/walkthrough.md").read_text()
headings = re.findall(r"^## (.+)$", walkthrough, re.M)
anchors = {re.sub(r"[^\w\- ]", "", s.lower()).replace(" ", "-") for s in headings}
for name in sys.argv[2:]:
    path = Path(name)
    parser = module.parsed_document(path.read_text())
    accepted = []
    link_errors = []
    for tag, rel, value in parser.references:
        error = module.reference_error(tag, rel, value)
        url = urlparse(value)
        prefix = f"/agno-agi/agno/blob/{REV}/"
        source = url.path.startswith(prefix)
        revision = url.path == f"/agno-agi/agno/tree/{REV}"
        walk = url.path == "/intellif-aied/repo-reading/blob/master/agno-analysis/walkthrough.md"
        if error and tag == "a" and url.scheme == "https" and url.netloc == "github.com" and (source or revision or walk):
            accepted.append(error)
            if source:
                source_path = ROOT / "agno" / url.path[len(prefix):]
                if not source_path.is_file():
                    link_errors.append(f"源码文件不存在：{source_path}")
                elif url.fragment:
                    line = re.fullmatch(r"L(\d+)", url.fragment)
                    if not line or not 1 <= int(line[1]) <= len(source_path.read_text().splitlines()):
                        link_errors.append(f"源码行号无效：{value}")
            if walk and url.fragment and unquote(url.fragment) not in anchors:
                link_errors.append(f"walkthrough 章节不存在：{url.fragment}")
        elif tag == "a" and not url.scheme and url.path:
            if not (path.parent / unquote(url.path)).is_file():
                link_errors.append(f"本地页面不存在：{value}")
    original = module.verify(path)
    remaining = list(original)
    for exception in accepted:
        remaining.remove(exception)
    remaining.extend(link_errors)
    print(f"{path.name}：原检查 {len(original)} 项；已说明导航例外 {len(accepted)} 项；其他问题 {len(remaining)} 项。")
    for finding in remaining:
        print(f"  {finding}")
    failed |= bool(remaining)
raise SystemExit(1 if failed else 0)
