# Repo Reading

Source walkthroughs and architecture diagrams from repository reading sessions.

- [Browse all rendered HTML analyses](https://intellif-aied.github.io/repo-reading/)
- [LayerFS architecture](https://intellif-aied.github.io/repo-reading/layerfs-analysis/architecture.html)
- [LayerFS source walkthrough](layerfs-analysis/walkthrough.md)

## Publishing HTML

GitHub Pages deploys automatically after pushes to `master`. The workflow collects Git-tracked HTML files and static assets, preserves their paths, and generates a homepage linking to every HTML page. Original source checkouts such as `layerfs/` are ignored and are not published.

Add an HTML analysis and its CSS/images to Git, then commit and push. Use browser-accessible links for Markdown documents (GitHub blob URLs) and original source files (upstream URLs); those source files are not part of the Pages site.

To preview the build locally:

```sh
python3 tools/build_pages.py
python3 -m http.server 8000 --directory _site
```

Open `http://localhost:8000`. Only tracked assets are included, so stage newly added HTML/assets before building. `_site/` is generated and ignored.

Repository setting: **Settings → Pages → Source → GitHub Actions**. Deployments can also be rerun from the **Deploy GitHub Pages** workflow in Actions.
