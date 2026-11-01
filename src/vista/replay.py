"""Build a self-contained observation/action replay; never re-execute actions."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path


def build_replay(root: Path) -> Path:
    root = root.resolve(strict=True)
    rows = []
    # Reuse public context frames and the ARC visible frame archive. Host-private
    # grids/evaluator targets and credentials are deliberately not rendered.
    paths = sorted(
        set(root.glob("frames/*.png")) | set(root.rglob("context/visuals/*.png"))
    )
    for path in paths:
        rows.append(
            {
                "path": str(path.relative_to(root)),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "image": "data:image/png;base64,"
                + base64.b64encode(path.read_bytes()).decode(),
            }
        )
    events = []
    for path in sorted(root.rglob("events.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("event") not in {"private_failure", "handler_failure"}:
                events.append({"source": str(path.relative_to(root)), **event})
    for frame in rows:
        frame["events"] = [
            event
            for event in events
            if (
                frame["path"] in event.get("frames", [])
                or any(
                    view.get("sha256") == frame["sha256"]
                    for observation in event.get("observation", [])
                    for view in observation.get("views", [])
                )
            )
        ]
    calls = []
    for path in sorted(root.rglob("calls.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                calls.append(
                    {"source": str(path.relative_to(root)), **json.loads(line)}
                )
            except ValueError:
                continue
    result_path = root / "result.json"
    result = json.loads(result_path.read_text()) if result_path.is_file() else {}
    data = (
        json.dumps(
            {"frames": rows, "events": events, "calls": calls, "result": result},
            ensure_ascii=False,
        )
        .replace("<", "\\u003c")
        .replace("\u2028", "\\u2028")
    )
    document = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>VISTA 运行回放</title><style>
body{background:#10151e;color:#dce5f3;font:15px system-ui;margin:24px}
h1{font-size:24px} main{display:grid;grid-template-columns:minmax(350px,1fr) 1fr;gap:20px}
img{max-width:100%;max-height:68vh;image-rendering:pixelated;background:#000}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#192232;padding:16px;max-height:65vh;overflow:auto}
input{width:65%}button,select{padding:7px;margin:4px}small{color:#9fb0c7}
</style><h1>VISTA 运行回放</h1><small>浏览保存的画面、动作与耗时。播放不调用模型或执行游戏动作。</small>
<div><button id="prev">上一帧</button><input id="slider" type="range" min="0" value="0"><button id="next">下一帧</button></div>
<main><section><div id="label"></div><img id="frame"></section><section>
<select id="view"><option value="frame_events">当前画面的事件</option><option value="events">全部环境与工具事件</option><option value="calls">模型与动作耗时</option><option value="result">最终结果</option></select><pre id="log"></pre></section></main>
<script id="data" type="application/json">DATA</script><script>
const d=JSON.parse(document.querySelector('#data').textContent),s=document.querySelector('#slider');
s.max=Math.max(0,d.frames.length-1);
function show(){const n=Number(s.value),f=d.frames[n]; document.querySelector('#label').textContent=f?`${n+1}/${d.frames.length}  ${f.path}`:'没有保存画面';if(f)document.querySelector('#frame').src=f.image;logs();}
s.oninput=show;document.querySelector('#prev').onclick=()=>{s.value=Math.max(0,Number(s.value)-1);show()};
document.querySelector('#next').onclick=()=>{s.value=Math.min(Number(s.max),Number(s.value)+1);show()};
function logs(){const v=document.querySelector('#view').value;document.querySelector('#log').textContent=JSON.stringify(v==='frame_events'?(d.frames[Number(s.value)]?.events??[]):d[v],null,2)}
document.querySelector('#view').onchange=logs;show();logs();</script></html>""".replace(
        "DATA", data
    )
    output = root / "replay.html"
    output.write_text(document, encoding="utf-8")
    return output
