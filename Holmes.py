
"""
title: HolmesGPT SRE 5.3.2
author: SRE
version: 5.3.2
description: HolmesGPT SSE, Grafana images, interactive Prometheus charts and tool diagnostics.
requirements: httpx,pydantic
"""

import base64
import binascii
import hashlib
import html
import json
import re
import time
from typing import Any, Optional

import httpx
from pydantic import BaseModel, Field


# ============================================================
# Chart and image intent
# ============================================================

CHART_RE = re.compile(
    r"畫(?:個|一張|出|成)?圖|繪圖|圖表|趨勢圖|折線圖|曲線圖|"
    r"長條圖|柱狀圖|面積圖|散點圖|走勢圖|視覺化|可視化|"
    r"顯示趨勢|chart|graph|plot|visualiz",
    re.I,
)

CHART_NEG_RE = re.compile(
    r"(?:不要|不需(?:要)?|無需|不用|別|禁止|不必)"
    r"(?:.{0,12})(?:畫圖|圖表|趨勢圖|折線圖|曲線圖|"
    r"長條圖|視覺化|chart|graph|plot)",
    re.I,
)

IMAGE_RE = re.compile(
    r"圖片|截圖|影像|圖像|渲染|render|screenshot|"
    r"image|png|面板.*圖|panel.*圖",
    re.I,
)

IMAGE_NEG_RE = re.compile(
    r"(?:不要|不需(?:要)?|無需|不用|別|不必)"
    r".{0,12}(?:圖片|截圖|影像|渲染|render|image)",
    re.I,
)

IMAGE_TOOLS = {
    "grafana_render_panel",
    "grafana_render_dashboard",
    "get_panel_image",
}

MAX_IMAGE_BYTES = 8 * 1024 * 1024


def chart_requested(text: str) -> bool:
    return bool(CHART_RE.search(text or "")) and not bool(
        CHART_NEG_RE.search(text or "")
    )


def image_requested(text: str) -> bool:
    return bool(IMAGE_RE.search(text or "")) and not bool(
        IMAGE_NEG_RE.search(text or "")
    )


# ============================================================
# Prometheus matrix extraction
# ============================================================

def unpack_matrix(value: Any, depth: int = 0) -> Optional[dict]:

    if depth >= 8:
        return None

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return None

    if isinstance(value, list):
        for elem in value[:20]:
            found = unpack_matrix(elem, depth + 1)
            if found:
                return found
        return None

    if not isinstance(value, dict):
        return None

    if (
        value.get("resultType") == "matrix"
        and isinstance(value.get("result"), list)
    ):
        return value

    result = value.get("result")

    if isinstance(result, list) and any(
        isinstance(s, dict) and "values" in s
        for s in result[:20]
    ):
        return {
            "resultType": "matrix",
            "result": result,
        }

    for key in (
        "data",
        "result",
        "output",
        "content",
        "response",
    ):
        if key in value:
            found = unpack_matrix(value[key], depth + 1)
            if found:
                return found

    return None


# ============================================================
# Diagnostic safety
# ============================================================

def redact(value: Any, max_chars: int = 600) -> str:

    if value is None:
        return ""

    if not isinstance(value, str):
        try:
            value = json.dumps(
                value,
                ensure_ascii=False,
                default=str,
            )
        except Exception:
            value = str(value)

    value = re.sub(
        r"(?i)\b(Bearer\s+)[^\s\"'<>]+",
        r"\1[REDACTED]",
        value,
    )

    value = re.sub(
        r'(?i)(["\']?(?:authorization|api[_-]?key|'
        r'access[_-]?token|id[_-]?token|password|secret)'
        r'["\']?\s*[:=]\s*)(["\']?)[^\s,"\'&}]+',
        r"\1[REDACTED]",
        value,
    )

    value = re.sub(
        r"(?i)([?&](?:token|access_token|api_key|key)=)[^&#\s]+",
        r"\1[REDACTED]",
        value,
    )

    return value[:max_chars]


def safe_params(value: Any) -> str:

    if not isinstance(value, dict):
        return ""

    allowed = {
        "query",
        "expr",
        "promql",
        "type",
        "limit",
        "page",
        "tag",
        "starred",
        "dashboardIds",
        "dashboardUIDs",
        "folderUIDs",
        "uid",
        "from",
        "to",
        "step",
        "start",
        "end",
    }

    filtered = {
        key: val
        for key, val in value.items()
        if key in allowed
    }

    return redact(filtered, 500) if filtered else ""


def tool_error(result: dict, payload: dict) -> str:

    for source in (result, payload):

        if not isinstance(source, dict):
            continue

        for key in ("error", "message", "detail"):
            value = source.get(key)

            if value:
                return redact(value, 700)

    data = result.get("data")

    if isinstance(data, dict):
        return redact(
            data.get("error") or data.get("message") or "",
            700,
        )

    return ""


def extract_text(value: Any) -> str:

    if isinstance(value, str):
        return value.strip()

    if isinstance(value, dict):

        for key in (
            "analysis",
            "answer",
            "content",
            "text",
            "message",
        ):
            result = extract_text(value.get(key))

            if result:
                return result

    if isinstance(value, list):
        parts = [extract_text(x) for x in value]
        return "\n".join(
            part for part in parts if part
        ).strip()

    return ""


# ============================================================
# Interactive Prometheus chart
# Retains:
# - Line
# - Area
# - Bars
# - Points
# - Y axis toggle
# - Tooltip
# - Statistics
# - PromQL
# - Taiwan time
# ============================================================

def chart_html(
    matrix: dict,
    tool_result: dict,
) -> Optional[str]:

    raw_series = matrix.get("result", [])
    series = []

    for raw in raw_series[:10]:

        if not isinstance(raw, dict):
            continue

        points = []

        for x in raw.get("values", [])[:2000]:

            try:
                ts = float(x[0])
                val = float(x[1])

                if val == val and abs(val) != float("inf"):
                    points.append([
                        int(ts * 1000),
                        val,
                    ])

            except (
                ValueError,
                TypeError,
                IndexError,
                OverflowError,
            ):
                pass

        if not points:
            continue

        metric = raw.get("metric") or {}

        name = (
            metric.get("pod")
            or metric.get("container")
            or metric.get("__name__")
            or "Series"
        )

        series.append({
            "name": str(name),
            "points": points,
        })

    if not series:
        return None

    params = tool_result.get("params") or {}

    if not isinstance(params, dict):
        params = {}

    promql = (
        params.get("query")
        or params.get("expr")
        or params.get("promql")
        or ""
    )

    payload = json.dumps(
        {
            "series": series,
            "promql": promql,
        },
        ensure_ascii=False,
    )

    payload = (
        payload
        .replace("<", "\\u003c")
        .replace("</", "<\\/")
    )

    return """
<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">

<style>
body {
    margin: 0;
    background: #111827;
    color: #edf2fa;
    font: 14px system-ui,-apple-system,"Segoe UI",sans-serif;
}

.wrap {
    padding: 16px;
}

.meta {
    color: #a9b7ca;
    font-size: 12px;
    margin: 6px 0 14px;
}

.tools {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-bottom: 12px;
}

button {
    background: #273349;
    border: 1px solid #506078;
    border-radius: 6px;
    color: #e4edff;
    padding: 7px 10px;
    cursor: pointer;
}

button.active {
    background: #0d7896;
    border-color: #24d5ef;
}

.plot {
    position: relative;
}

canvas {
    width: 100%;
    height: 290px;
    display: block;
    touch-action: none;
}

.tip {
    display: none;
    position: absolute;
    pointer-events: none;
    background: #030712ed;
    border: 1px solid #6a7892;
    padding: 7px 9px;
    border-radius: 6px;
    font-size: 12px;
    white-space: pre;
}

.stats {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 12px;
}

.stat {
    background: #263347;
    border-radius: 8px;
    padding: 8px 10px;
    min-width: 100px;
}

.stat b {
    display: block;
    font-size: 15px;
    color: white;
    margin-top: 3px;
}

details {
    margin-top: 14px;
    color: #b8c7d9;
}

pre {
    white-space: pre-wrap;
    word-break: break-all;
    font-size: 12px;
}

small {
    color: #a7b6ca;
}
</style>
</head>

<body>
<div class="wrap">

<strong>Prometheus 互動式趨勢圖</strong>

<div class="meta" id="meta"></div>

<div class="tools">
    <button data-mode="line" class="active">Line 折線</button>
    <button data-mode="area">Area 面積</button>
    <button data-mode="bars">Bars 柱狀</button>
    <button data-mode="points">Points 散點</button>
    <button id="axis">Y 軸：自動</button>
</div>

<div class="plot">
    <canvas id="c"></canvas>
    <div id="tip" class="tip"></div>
</div>

<div class="stats" id="stats"></div>

<details id="querybox">
    <summary>查看 PromQL</summary>
    <pre id="query"></pre>
</details>

</div>

<script>
const D = __DATA__;
const S = D.series;

const canvas = document.getElementById('c');
const ctx = canvas.getContext('2d');
const tip = document.getElementById('tip');

let mode = 'line';
let zero = false;
let layout = {};

const colors = [
    '#41dbe8',
    '#f59e0b',
    '#a78bfa',
    '#4ade80',
    '#fb7185',
    '#60a5fa'
];

const all = S.flatMap(s => s.points);

const xs = all.map(x => x[0]);
const ys = all.map(x => x[1]);

let xmin = Math.min(...xs);
let xmax = Math.max(...xs);
let ymin = Math.min(...ys);
let ymax = Math.max(...ys);

if (xmax <= xmin) {
    xmax = xmin + 1000;
}

if (ymax <= ymin) {
    ymax += Math.max(.0001, Math.abs(ymax) * .01);
    ymin -= Math.max(.0001, Math.abs(ymin) * .01);
}

const fmt = n =>
    Number(n).toLocaleString('en-US', {
        maximumFractionDigits: 5
    });

const date = ms =>
    new Date(ms).toLocaleString('zh-TW', {
        timeZone: 'Asia/Taipei',
        hour12: false,
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });

document.getElementById('meta').textContent =
    '台灣時間 UTC+8 ・ ' +
    date(xmin) +
    ' ~ ' +
    date(xmax) +
    ' ・ ' +
    S.length +
    ' 條序列';

document.getElementById('query').textContent =
    D.promql || '未提供 PromQL';

if (!D.promql) {
    document.getElementById('querybox').style.display = 'none';
}

const escapeHtml = text =>
    String(text).replace(
        /[&<>"']/g,
        c => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;'
        }[c])
    );

document.getElementById('stats').innerHTML =
    S.slice(0, 4).map(s => {

        const v = s.points.map(p => p[1]);

        const sum = v.reduce(
            (a, b) => a + b,
            0
        );

        return `
        <div class="stat">
            <small>${escapeHtml(s.name)}</small>
            <b>最新 ${fmt(v[v.length - 1])}</b>
            <small>
                平均 ${fmt(sum / v.length)}
                ・ 最大 ${fmt(Math.max(...v))}
                ・ 最小 ${fmt(Math.min(...v))}
            </small>
        </div>`;
    }).join('');


function draw() {

    const w = canvas.getBoundingClientRect().width || 720;
    const h = 290;
    const ratio = devicePixelRatio || 1;

    canvas.width = Math.round(w * ratio);
    canvas.height = Math.round(h * ratio);

    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, w, h);

    const l = 65;
    const r = 12;
    const t = 14;
    const b = 40;

    const pw = w - l - r;
    const ph = h - t - b;

    const lo = zero ? Math.min(0, ymin) : ymin;
    const hi = Math.max(ymax, lo + 1e-12);

    const dy = Math.max(1e-12, hi - lo);

    layout = {
        l, r, t, b, pw, ph, w, h, lo, hi
    };

    const px = x =>
        l + (x - xmin) / (xmax - xmin) * pw;

    const py = y =>
        t + (hi - y) / dy * ph;

    ctx.font = '11px system-ui';
    ctx.strokeStyle = '#374357';
    ctx.fillStyle = '#c4d3e8';
    ctx.lineWidth = 1;

    for (let i = 0; i <= 4; i++) {

        const y = t + ph * i / 4;

        ctx.beginPath();
        ctx.moveTo(l, y);
        ctx.lineTo(w - r, y);
        ctx.stroke();

        ctx.fillText(
            fmt(hi - dy * i / 4),
            4,
            y + 4
        );
    }

    for (let i = 0; i <= 4; i++) {

        const x = l + pw * i / 4;

        ctx.fillText(
            new Date(
                xmin + (xmax - xmin) * i / 4
            ).toLocaleTimeString('zh-TW', {
                timeZone: 'Asia/Taipei',
                hour: '2-digit',
                minute: '2-digit',
                hour12: false
            }),
            Math.max(l, x - 20),
            h - 11
        );
    }

    S.forEach((s, i) => {

        const p = s.points;
        const c = colors[i % colors.length];

        ctx.strokeStyle = c;
        ctx.fillStyle = c;
        ctx.lineWidth = 2;

        ctx.beginPath();

        p.forEach((v, j) => {
            if (j === 0) {
                ctx.moveTo(px(v[0]), py(v[1]));
            } else {
                ctx.lineTo(px(v[0]), py(v[1]));
            }
        });

        if (mode === 'area') {

            ctx.lineTo(
                px(p[p.length - 1][0]),
                py(lo)
            );

            ctx.lineTo(
                px(p[0][0]),
                py(lo)
            );

            ctx.closePath();
            ctx.globalAlpha = .22;
            ctx.fill();
            ctx.globalAlpha = 1;

            ctx.beginPath();

            p.forEach((v, j) =>
                j
                    ? ctx.lineTo(px(v[0]), py(v[1]))
                    : ctx.moveTo(px(v[0]), py(v[1]))
            );

            ctx.stroke();

        } else if (mode === 'line') {

            ctx.stroke();

        } else if (mode === 'points') {

            for (const v of p) {
                ctx.beginPath();
                ctx.arc(
                    px(v[0]),
                    py(v[1]),
                    3,
                    0,
                    Math.PI * 2
                );
                ctx.fill();
            }

        } else {

            const bw = Math.max(
                2,
                Math.min(
                    14,
                    pw / (p.length * S.length) * .7
                )
            );

            p.forEach(v => {
                ctx.fillRect(
                    px(v[0]) - bw / 2,
                    Math.min(py(v[1]), py(lo)),
                    bw,
                    Math.max(
                        1,
                        Math.abs(py(lo) - py(v[1]))
                    )
                );
            });
        }
    });
}

document.querySelectorAll('[data-mode]').forEach(b => {

    b.onclick = () => {

        mode = b.dataset.mode;

        document.querySelectorAll('[data-mode]').forEach(x => {
            x.classList.toggle(
                'active',
                x === b
            );
        });

        draw();
    };
});

document.getElementById('axis').onclick = () => {

    zero = !zero;

    document.getElementById('axis').textContent =
        zero ? 'Y 軸：含 0' : 'Y 軸：自動';

    draw();
};

canvas.addEventListener('mousemove', e => {

    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;

    if (x < layout.l || x > layout.w - layout.r) {
        tip.style.display = 'none';
        return;
    }

    const ts =
        xmin +
        (x - layout.l) /
        layout.pw *
        (xmax - xmin);

    let nearest = null;
    let dist = Infinity;

    S.forEach((s, i) => {

        s.points.forEach(p => {

            const d = Math.abs(p[0] - ts);

            if (d < dist) {
                nearest = {
                    p,
                    i,
                    name: s.name
                };

                dist = d;
            }
        });
    });

    if (!nearest) {
        return;
    }

    tip.textContent =
        nearest.name +
        '\\n' +
        date(nearest.p[0]) +
        '\\n' +
        fmt(nearest.p[1]);

    tip.style.display = 'block';

    tip.style.left =
        Math.min(x + 10, layout.w - 155) + 'px';

    tip.style.top = '20px';
});

canvas.addEventListener('mouseleave', () => {
    tip.style.display = 'none';
});

if (typeof ResizeObserver !== 'undefined') {

    new ResizeObserver(draw).observe(
        canvas.parentElement
    );

} else {

    window.addEventListener('resize', draw);
}

draw();
</script>
</body>
</html>
""".replace("__DATA__", payload)


# ============================================================
# Grafana rendered image handling
# ============================================================

def normalize_image(candidate: Any) -> Optional[str]:

    if isinstance(candidate, dict):
        candidate = (
            candidate.get("data")
            or candidate.get("base64")
            or candidate.get("image")
            or candidate.get("url")
            or candidate.get("src")
            or candidate.get("content")
        )

    if not isinstance(candidate, str):
        return None

    candidate = candidate.strip()

    if candidate.startswith("data:image/"):
        candidate = candidate.split(",", 1)[-1]

    elif candidate.startswith(("http://", "https://")):
        # Do not expose potentially private internal URLs.
        return None

    if not candidate or len(candidate) > MAX_IMAGE_BYTES * 2:
        return None

    try:
        raw = base64.b64decode(
            candidate,
            validate=True,
        )
    except (ValueError, binascii.Error):
        return None

    if not raw or len(raw) > MAX_IMAGE_BYTES:
        return None

    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"

    elif raw.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"

    elif raw.startswith((b"GIF87a", b"GIF89a")):
        mime = "image/gif"

    elif (
        raw.startswith(b"RIFF")
        and raw[8:12] == b"WEBP"
    ):
        mime = "image/webp"

    else:
        return None

    return (
        f"data:{mime};base64,"
        + base64.b64encode(raw).decode("ascii")
    )


def extract_render_images(result: dict) -> list[str]:

    if not isinstance(result, dict):
        return []

    sources = []

    for key in ("images", "image"):

        if result.get(key) is not None:
            sources.append(result[key])

    data = result.get("data")

    if isinstance(data, dict):

        for key in ("images", "image"):

            if data.get(key) is not None:
                sources.append(data[key])

        if data.get("type") == "image":
            sources.append(data)

    elif isinstance(data, list):

        sources.append([
            item
            for item in data
            if isinstance(item, dict)
            and item.get("type") == "image"
        ])

    output = []

    for source in sources:

        group = (
            source if isinstance(source, list)
            else [source]
        )

        for item in group[:5]:

            uri = normalize_image(item)

            if uri and uri not in output:
                output.append(uri)

    return output


def image_html(uri: str, title: str) -> str:

    title = html.escape(
        title,
        quote=True,
    )

    return (
        '<!doctype html>'
        '<html lang="zh-Hant">'
        '<head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" '
        'content="width=device-width,initial-scale=1">'
        '<style>'
        'body{margin:0;padding:12px;'
        'background:#111827;color:#eef2ff;'
        'font:14px system-ui}'
        'figure{margin:0}'
        'img{display:block;max-width:100%;'
        'width:auto;height:auto;margin:auto;'
        'border-radius:6px}'
        'figcaption{margin:8px 0;color:#b9c5d9}'
        '</style>'
        '</head>'
        '<body>'
        '<figure>'
        f'<img src="{uri}" alt="{title}">'
        f'<figcaption>{title}</figcaption>'
        '</figure>'
        '</body>'
        '</html>'
    )


# ============================================================
# HolmesGPT Pipe
# ============================================================

class Pipe:

    class Valves(BaseModel):

        HOLMES_URL: str = Field(
            default=(
                "http://holmesgpt-holmes."
                "holmesgpt.svc.cluster.local:80/api/chat"
            ),
            description="HolmesGPT API URL",
        )

        HOLMES_MODEL: str = Field(
            default="qwen-local"
        )

        TIMEOUT_SECONDS: int = Field(
            default=900,
            ge=30,
            le=3600,
        )

        ADMIN_ONLY: bool = Field(
            default=True
        )

        SHOW_TOOL_REPORT: bool = Field(
            default=True
        )

        SHOW_TOOL_ERRORS: bool = Field(
            default=True
        )

        SHOW_TOOL_PARAMS: bool = Field(
            default=True
        )

        SHOW_SSE_DIAGNOSTICS: bool = Field(
            default=True
        )

        MAX_CHARTS: int = Field(
            default=2,
            ge=0,
            le=5,
        )

        MAX_RENDER_IMAGES: int = Field(
            default=3,
            ge=0,
            le=5,
        )

    def __init__(self):

        self.type = "pipe"
        self.id = "holmesgpt_sre_v532"
        self.name = "HolmesGPT SRE 5.3.2"

        self.valves = self.Valves()

    def pipes(self):

        return [{
            "id": "sre",
            "name": "HolmesGPT SRE 5.3.2",
        }]

    async def pipe(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __event_emitter__=None,
    ):

        # ----------------------------------------------------
        # Open WebUI events
        # ----------------------------------------------------

        async def emit(
            event_type: str,
            data: dict,
        ):

            if __event_emitter__:
                await __event_emitter__({
                    "type": event_type,
                    "data": data,
                })

        async def status(
            description: str,
            done: bool = False,
        ):

            await emit(
                "status",
                {
                    "description": description,
                    "done": done,
                },
            )

        # ----------------------------------------------------
        # Authorization
        # ----------------------------------------------------

        user = __user__ or {}

        role = (
            user.get("role")
            if isinstance(user, dict)
            else getattr(user, "role", "")
        ) or ""

        if self.valves.ADMIN_ONLY and role != "admin":
            return (
                "此 HolmesGPT SRE Pipe "
                "目前僅限管理員使用。"
            )

        # ----------------------------------------------------
        # User question
        # ----------------------------------------------------

        messages = body.get("messages") or []
        question = ""

        for msg in reversed(messages):

            if not isinstance(msg, dict):
                continue

            if msg.get("role") != "user":
                continue

            content = msg.get("content", "")

            if isinstance(content, str):
                question = content.strip()

            elif isinstance(content, list):
                question = "\n".join(
                    str(x.get("text", ""))
                    for x in content
                    if isinstance(x, dict)
                    and x.get("type") == "text"
                ).strip()

            break

        if not question:
            return "沒有收到使用者問題。"

        show_chart = chart_requested(question)
        show_render_images = image_requested(question)

        await status(
            "HolmesGPT 正在開始調查..."
        )

        # ----------------------------------------------------
        # Investigation state
        # ----------------------------------------------------

        started = time.perf_counter()

        tool_runs = {}
        tool_order = []

        final_answer = ""
        error_text = ""

        finished = False

        charts = 0
        rendered_images = 0
        saw_render_success = False

        embeds = []
        image_hashes = set()

        event_counts = {}
        final_event_keys = []
        finish_reason = ""
        http_status = ""

        sse_event = ""
        sse_data = []

        # ----------------------------------------------------
        # Tool event matching
        # ----------------------------------------------------

        def find_run(key: str, tool: str) -> str:

            if key and key in tool_runs:
                return key

            for candidate in reversed(tool_order):

                run = tool_runs[candidate]

                if (
                    run["status"] == "running"
                    and (
                        not tool
                        or run["name"] == tool
                    )
                ):
                    return candidate

            unique = (
                key
                if key and key not in tool_runs
                else f"result-{len(tool_order) + 1}"
            )

            tool_runs[unique] = {
                "name": tool or "tool",
                "start": None,
                "status": "running",
                "seconds": None,
                "error": "",
                "params": "",
                "image_count": 0,
            }

            tool_order.append(unique)

            return unique

        # ----------------------------------------------------
        # SSE handler
        # ----------------------------------------------------

        async def handle(
            event_name: str,
            payload: dict,
        ):

            nonlocal final_answer
            nonlocal error_text
            nonlocal finished
            nonlocal charts
            nonlocal rendered_images
            nonlocal saw_render_success
            nonlocal final_event_keys
            nonlocal finish_reason

            if not isinstance(payload, dict):
                payload = {"content": payload}

            name = (
                event_name
                or str(
                    payload.get("event")
                    or payload.get("type")
                    or ""
                )
            )

            event_counts[name or "(unnamed)"] = (
                event_counts.get(name or "(unnamed)", 0)
                + 1
            )

            reason = payload.get("finish_reason")

            if reason:
                finish_reason = redact(reason, 120)

            # -----------------------------------------------
            # Tool start
            # -----------------------------------------------

            if name == "start_tool_calling":

                key = str(
                    payload.get("id")
                    or payload.get("tool_call_id")
                    or f"tool-{len(tool_order) + 1}"
                )

                if key in tool_runs:
                    key = (
                        f"{key}-"
                        f"{len(tool_order) + 1}"
                    )

                tool = str(
                    payload.get("tool_name")
                    or payload.get("name")
                    or "tool"
                )

                tool_runs[key] = {
                    "name": tool,
                    "start": time.perf_counter(),
                    "status": "running",
                    "seconds": None,
                    "error": "",
                    "params": safe_params(
                        payload.get("params")
                    ),
                    "image_count": 0,
                }

                tool_order.append(key)

                await status(
                    f"正在執行 {tool}..."
                )

            # -----------------------------------------------
            # Tool result
            # -----------------------------------------------

            elif name == "tool_calling_result":

                raw_result = payload.get("result")

                if isinstance(raw_result, dict):
                    result = raw_result
                else:
                    result = {
                        "data": raw_result
                    }

                key = str(
                    payload.get("tool_call_id")
                    or payload.get("id")
                    or ""
                )

                tool = str(
                    payload.get("name")
                    or payload.get("tool_name")
                    or ""
                )

                key = find_run(
                    key,
                    tool,
                )

                item = tool_runs[key]

                if tool:
                    item["name"] = tool

                tool = item["name"]

                item["status"] = str(
                    result.get("status")
                    or payload.get("status")
                    or "unknown"
                ).lower()

                item["error"] = tool_error(
                    result,
                    payload,
                )

                item["params"] = (
                    safe_params(
                        result.get("params")
                        or payload.get("params")
                    )
                    or item["params"]
                )

                item["seconds"] = (
                    round(
                        time.perf_counter()
                        - item["start"],
                        2,
                    )
                    if item["start"] is not None
                    else None
                )

                await status(
                    f"{tool} 執行"
                    + (
                        "成功"
                        if item["status"] == "success"
                        else (
                            "結束："
                            + item["status"]
                        )
                    )
                )

                # -------------------------------------------
                # Grafana rendered images
                # -------------------------------------------

                if (
                    tool in IMAGE_TOOLS
                    and item["status"] == "success"
                ):

                    saw_render_success = True

                    if show_render_images:

                        image_uris = extract_render_images(
                            result
                        )

                        if not image_uris:
                            image_uris = extract_render_images(
                                payload
                            )

                        item["image_count"] = len(
                            image_uris
                        )

                        for uri in image_uris:

                            if (
                                rendered_images
                                >= self.valves.MAX_RENDER_IMAGES
                            ):
                                break

                            digest = hashlib.sha256(
                                uri.encode("ascii")
                            ).hexdigest()

                            if digest in image_hashes:
                                continue

                            image_hashes.add(digest)

                            embeds.append(
                                image_html(
                                    uri,
                                    "Grafana Panel 圖片",
                                )
                            )

                            rendered_images += 1

                # -------------------------------------------
                # Prometheus interactive charts
                # -------------------------------------------

                if (
                    show_chart
                    and charts < self.valves.MAX_CHARTS
                    and tool in (
                        "execute_prometheus_range_query",
                        "query_prometheus",
                    )
                    and item["status"] == "success"
                ):

                    matrix = unpack_matrix(
                        result
                    )

                    if matrix:

                        markup = chart_html(
                            matrix,
                            result,
                        )

                        if markup:

                            embeds.append(markup)

                            charts += 1

            # -----------------------------------------------
            # AI intermediate message
            # -----------------------------------------------

            elif name == "ai_message":

                await status(
                    "AI 正在分析調查結果..."
                )

            # -----------------------------------------------
            # AI final answer
            # -----------------------------------------------

            elif name == "ai_answer_end":

                finished = True
                final_event_keys = sorted(
                    payload.keys()
                )

                answer = (
                    extract_text(payload.get("analysis"))
                    or extract_text(payload.get("answer"))
                    or extract_text(payload.get("content"))
                )

                if answer:
                    final_answer = answer

            # -----------------------------------------------
            # Errors
            # -----------------------------------------------

            elif name in (
                "error",
                "stream_error",
                "ai_error",
            ):

                error_text = redact(
                    payload.get("error")
                    or payload.get("message")
                    or payload.get("detail")
                    or payload,
                    1000,
                )

        # ----------------------------------------------------
        # SSE parser
        # ----------------------------------------------------

        async def flush_sse():

            nonlocal sse_event
            nonlocal sse_data

            if sse_data:

                content = "\n".join(sse_data)

                if content != "[DONE]":

                    try:
                        payload = json.loads(
                            content
                        )

                    except (TypeError, ValueError):
                        payload = {
                            "message": content
                        }

                    await handle(
                        sse_event,
                        payload,
                    )

            sse_event = ""
            sse_data = []

        # ----------------------------------------------------
        # HolmesGPT API request
        # ----------------------------------------------------

        try:

            timeout = httpx.Timeout(
                connect=15.0,
                read=float(
                    self.valves.TIMEOUT_SECONDS
                ),
                write=30.0,
                pool=15.0,
            )

            async with httpx.AsyncClient(
                timeout=timeout,
                trust_env=False,
            ) as client:

                async with client.stream(
                    "POST",
                    self.valves.HOLMES_URL,
                    json={
                        "ask": question,
                        "model": self.valves.HOLMES_MODEL,
                        "stream": True,
                        "enable_tool_approval": False,
                    },
                    headers={
                        "Accept": "text/event-stream",
                        "Content-Type": "application/json",
                    },
                ) as response:

                    http_status = str(
                        response.status_code
                    )

                    response.raise_for_status()

                    async for line in response.aiter_lines():

                        if not line:
                            await flush_sse()

                        elif line.startswith("event:"):
                            sse_event = line[6:].strip()

                        elif line.startswith("data:"):
                            sse_data.append(
                                line[5:].lstrip()
                            )

                    await flush_sse()

        except httpx.HTTPStatusError as exc:

            error_text = redact(
                f"HolmesGPT HTTP "
                f"{exc.response.status_code}: "
                f"{exc.response.text[:500]}",
                800,
            )

        except httpx.TimeoutException:

            error_text = (
                f"HolmesGPT 超過 "
                f"{self.valves.TIMEOUT_SECONDS} "
                f"秒未完成傳輸"
            )

        except Exception as exc:

            error_text = redact(
                f"{type(exc).__name__}: {exc}",
                800,
            )

        # ----------------------------------------------------
        # Open WebUI visualization event
        #
        # Both Grafana images and Prometheus charts use embeds.
        # They remain separate visualization items.
        # ----------------------------------------------------

        if embeds:

            await emit(
                "embeds",
                {
                    "embeds": embeds,
                    "replace": True,
                },
            )

        # ----------------------------------------------------
        # Final investigation report
        # ----------------------------------------------------

        elapsed = (
            time.perf_counter() - started
        )

        await status(
            f"調查結束，耗時 {elapsed:.2f} 秒",
            done=True,
        )

        report = [
            f"**調查耗時：{elapsed:.2f} 秒**"
        ]

        if final_answer:

            report.append(
                final_answer
            )

        elif error_text:

            report.append(
                "**調查未完成**："
                + error_text
            )

        elif not finished:

            report.append(
                "**調查未完成**："
                "HolmesGPT 串流結束，"
                "但未收到 `ai_answer_end`。"
            )

        else:

            report.append(
                "**調查未完成**："
                "收到 `ai_answer_end`，"
                "但最終回答為空。"
                "請檢查 Qwen 輸出或 SSE 事件。"
            )

        # ----------------------------------------------------
        # Image result verification
        # ----------------------------------------------------

        if (
            show_render_images
            and saw_render_success
            and rendered_images == 0
        ):

            report.append(
                "**圖片尚未成功顯示**："
                "Grafana Rendering 工具回報成功，"
                "但 SSE 未包含可辨識的圖片資料。"
                "請檢查 "
                "`tool_calling_result.result.images`。"
            )

        elif rendered_images:

            report.append(
                f"**Grafana 圖片："
                f"已附加 {rendered_images} 張。**"
            )

        # ----------------------------------------------------
        # Tool execution report
        # ----------------------------------------------------

        if self.valves.SHOW_TOOL_REPORT:

            report.append(
                "**工具執行紀錄**"
            )

            if not tool_order:

                report.append(
                    "未收到工具執行事件。"
                )

            for key in tool_order:

                item = tool_runs[key]

                duration = (
                    f"，{item['seconds']:.2f}s"
                    if item["seconds"] is not None
                    else ""
                )

                ok = (
                    item["status"] == "success"
                )

                symbol = (
                    "✓" if ok else "✗"
                )

                report.append(
                    f"- {symbol} "
                    f"`{item['name']}`："
                    f"{item['status']}"
                    f"{duration}"
                )

                if (
                    self.valves.SHOW_TOOL_PARAMS
                    and item.get("params")
                ):

                    report.append(
                        f"  - 參數："
                        f"`{item['params']}`"
                    )

                if (
                    not ok
                    and self.valves.SHOW_TOOL_ERRORS
                ):

                    error = (
                        item.get("error")
                        or "工具沒有提供錯誤內容"
                    )

                    report.append(
                        f"  - 錯誤：{error}"
                    )

                if (
                    item["name"] in IMAGE_TOOLS
                    and show_render_images
                ):

                    report.append(
                        "  - 辨識到的圖片："
                        f"{item.get('image_count', 0)} 張"
                    )

            report.append(
                f"工具 {len(tool_order)} 次；"
                f"互動圖表 {charts} 張；"
                f"Grafana 圖片 {rendered_images} 張。"
            )

        # ----------------------------------------------------
        # SSE diagnostics
        # ----------------------------------------------------

        if (
            self.valves.SHOW_SSE_DIAGNOSTICS
            and (
                not final_answer
                or error_text
            )
        ):

            report.append(
                "**SSE 診斷摘要**"
            )

            report.append(
                f"- HTTP："
                f"{http_status or '未知'}"
            )

            report.append(
                "- 最終事件欄位："
                + (
                    ", ".join(final_event_keys)
                    if final_event_keys
                    else "未收到"
                )
            )

            report.append(
                "- finish_reason："
                + (
                    finish_reason
                    or "未提供"
                )
            )

            counts = ", ".join(
                f"{key}={value}"
                for key, value in sorted(
                    event_counts.items()
                )
            )

            report.append(
                "- 事件計數："
                + (
                    redact(counts, 700)
                    or "無"
                )
            )

        return "\n\n".join(report)
