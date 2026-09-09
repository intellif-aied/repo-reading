"""验证导航层级、静态链接与 Git 跟踪边界。"""
from html.parser import HTMLParser
from pathlib import Path
import subprocess
import tempfile
import unittest

from build_pages import build_site


class Links(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.urls = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.urls.append(dict(attrs).get('href'))


class BuildPagesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)

    def write(self, name, content, tracked=True):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        if tracked:
            subprocess.run(['git', 'add', '--', name], cwd=self.root, check=True)

    def links(self, name):
        return Links((self.root / '_site' / name).read_text()).urls

    def test_one_homepage_entry_per_repo_with_nested_analysis(self):
        self.write('teamai-cli-analysis/architecture.html', '<title>架构图</title>')
        self.write('teamai-cli-analysis/v2/flow chart.html', '<title>新版流程图</title>')
        self.write('teamai-cli-analysis/walkthrough.md', '# 讲解')
        self.write('layerfs-analysis/architecture.html', '<title>LayerFS</title>')
        self.write('teamai-cli-analysis/draft.html', '<title>未完成</title>', tracked=False)
        self.write('teamai-cli-analysis/v2/style.css', 'body {color: black}')
        build_site(self.root)
        local = [u for u in self.links('index.html') if not u.startswith('https:')]
        self.assertEqual(local, ['layerfs-analysis/', 'teamai-cli-analysis/'])
        detail = self.links('teamai-cli-analysis/index.html')
        self.assertIn('architecture.html', detail)
        self.assertIn('v2/flow%20chart.html', detail)
        self.assertIn('../', detail)
        self.assertTrue(any(u.endswith('/walkthrough.md') for u in detail))
        self.assertNotIn('draft.html', detail)
        self.assertFalse((self.root / '_site/teamai-cli-analysis/draft.html').exists())
        self.assertTrue((self.root / '_site/teamai-cli-analysis/v2/style.css').is_file())

    def test_preserves_curated_repository_homepage(self):
        self.write('sample-analysis/index.html', '<title>人工目录</title><a href="flow.html">流程</a>')
        self.write('sample-analysis/flow.html', '<title>流程</title>')
        build_site(self.root)
        self.assertEqual((self.root / '_site/sample-analysis/index.html').read_text(),
                         (self.root / 'sample-analysis/index.html').read_text())
        self.assertEqual(self.links('index.html').count('sample-analysis/'), 1)

    def test_titles_are_escaped_and_old_pages_removed(self):
        self.write('demo-analysis/flow.html', '<title>&lt;script&gt; &amp; 图</title>')
        self.write('_site/stale.html', '旧页面', tracked=False)
        build_site(self.root)
        html = (self.root / '_site/demo-analysis/index.html').read_text()
        self.assertIn('&lt;script&gt; &amp; 图', html)
        self.assertNotIn('<script>', html)
        self.assertFalse((self.root / '_site/stale.html').exists())


if __name__ == '__main__':
    unittest.main()
