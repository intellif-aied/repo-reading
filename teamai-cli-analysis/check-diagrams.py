"""保留原自检结果，仅为明确的 GitHub 源码导航链接记录例外。"""
import importlib.util
import sys
from pathlib import Path
from urllib.parse import urlparse

checker=Path(sys.argv[1])
spec=importlib.util.spec_from_file_location('diagram_self_check',checker)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
failed=False
for name in sys.argv[2:]:
    path=Path(name)
    parser=module.parsed_document(path.read_text())
    accepted=[]
    unexpected=[]
    for tag,rel,value in parser.references:
        error=module.reference_error(tag,rel,value)
        if error:
            url=urlparse(value)
            source=url.path.startswith('/Tencent/teamai-cli/blob/8d3d66db49ffed1b053faea9481f46e63b9ccc47/')
            walkthrough=url.path=='/intellif-aied/repo-reading/blob/master/teamai-cli-analysis/walkthrough.md'
            if tag=='a' and url.scheme=='https' and url.netloc=='github.com' and (source or walkthrough):
                accepted.append(error)
            else:
                unexpected.append(error)
    errors=module.verify(path)
    remaining=list(errors)
    for exception in accepted:
        remaining.remove(exception)
    assert not unexpected or remaining
    print(f'{path.name}：原检查报告 {len(errors)} 项；其中 {len(accepted)} 项为规范要求的 GitHub 导航链接；其他问题 {len(remaining)} 项。')
    if remaining:
        print('\n'.join(remaining))
        failed=True
raise SystemExit(1 if failed else 0)
