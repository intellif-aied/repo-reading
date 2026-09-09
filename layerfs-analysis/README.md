# LayerFS source analysis

- [Linear source walkthrough](walkthrough.md): 30 sections, with 78 executable source excerpts captured by Showboat.
- [Rendered architecture diagram](https://intellif-aied.github.io/repo-reading/layerfs-analysis/architecture.html): served by GitHub Pages; [HTML source](architecture.html), with inline SVG/CSS and optional Google Fonts.

Analyzed source revision: `46308986aec091337573227ede6c35aff7db11f2` (LayerFS 0.1.3).

This directory contains analysis artifacts only. The original LayerFS checkout is separate at `../layerfs/` and is excluded from this parent repository. Source links require that checkout to be present.

To replay the excerpts, use the analyzed source revision and run from this directory:

```sh
uvx showboat --workdir ../layerfs verify walkthrough.md
```

The source excerpts, links, diagram accessibility and static geometry were checked. These checks do not execute the LayerFS application test suite. Browser screenshot validation was unavailable because no browser executable was installed.
