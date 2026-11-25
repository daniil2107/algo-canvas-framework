# Algorithm Canvas

Algorithm Canvas is a local visual editor for studying one algorithm. Describe the problem, break the algorithm into stages, and compare versions by choosing a block for each stage. The problem graph connects design choices to insights and results from source documents.

## Run locally

From the repository root, run:

```sh
python3 server.py
```

Open [the canvas](http://127.0.0.1:8770/). A fresh checkout starts with an empty canvas. Add an objective and a setting, then a stage describing one job in the algorithm. Add blocks for different ways of doing that job. A version records the chosen blocks and can be forked for comparison.

The server uses the Python standard library and binds to the loopback interface. The page loads KaTeX from a CDN for formula rendering. No build step is required.

## Track issues

Under **Problem → Issues**, enter a title and choose **Add issue**. Use **Expected improvement** to describe what would get better if the issue were solved. Leave **Entire canvas** selected or choose the version it applies to.

Resolve an issue when the work is complete. You can reopen it later. Issues are saved in the local canvas alongside the algorithm.

## Where work is saved

The server saves the active canvas to `algorithm.json` next to `server.py`. This file includes insights, stages, blocks, versions, the problem graph, and imported research cards. Put local source documents in `theory/sources/` and Markdown cards in `theory/cards/`. Previous saves may appear in `.snapshots/`. Git ignores the live canvas, the theory directory and the snapshots.

Use a separate checkout for each canvas. To run two at once, give the second server a different port, for example `python3 server.py --port 8771`, and open that port in the browser.

## Research tools

`canvas_mcp.py` exposes the canvas over MCP. An agent can read the graph, inspect a stage in a particular version, search cards, and submit revision-checked research additions. Reads work directly from disk. Writes require the HTTP server for the same checkout. When changing the HTTP port, pass the same `--port` to the MCP command in the client configuration.

The repository includes project configurations for Codex and Claude Code and [skills](.agents/skills) for decomposing a paper, connecting a result to a stage, and developing a sourced alternative. The bundled morphological synthesis skill includes its [upstream license](.agents/skills/morphological-synthesis/LICENSE).

## Tests

Run `python3 -B -m unittest discover`. The browser tests use Chrome or Chromium and create their own temporary canvas. Set `CHROME` to the browser executable if it is installed outside the paths detected by the test driver.
