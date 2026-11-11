
"""
title: HolmesGPT SRE 5.3.0
author: SRE
version: 5.3.0
description: HolmesGPT SSE + optional interactive Prometheus charts (explicit chart intent only).
requirements: httpx,pydantic
"""

import asyncio
import html
import json
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from pydantic import BaseModel, Field


CHART_RE = re.compile(
    r"畫(?:個|一張|出|成)?圖|繪圖|圖表|趨勢圖|折線圖|曲線圖|長條圖|柱狀圖|面積圖|散點圖|走勢圖|視覺化|可視化|顯示趨勢|chart|graph|plot|visualiz",
    re.I,
)

CHART_NEG_RE = re.compile(
    r"(?:不要|不需(?:要)?|無需|不用|別|禁止|不必)(?:.{0,12})(?:畫圖|圖表|趨勢圖|折線圖|曲線圖|長條圖|視覺化|chart|graph|plot)",
    re.I,
)


def chart_requested(text: str) -> bool:
    return (
        bool(CHART_RE.search(text or ""))
        and not bool(CHART_NEG_RE.search(text or ""))
    )


def unpack_matrix(value: Any) -> Optional[dict]:
    """
    Accept Prometheus response and Holmes tool result wrappers.
    Bounded recursion.
    """
    for _ in range(7):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (ValueError, TypeError):
                return None

        if isinstance(value, list):
            for elem in value:
                found = unpack_matrix(elem)
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

        if (
            isinstance(value.get("result"), list)
            and any(
                isinstance(s, dict) and "values" in s
                for s in value["result"]
            )
        ):
            return {
                "resultType": "matrix",
                "result": value["result"],
            }

        for key in (
            "data",
            "result",
            "output",
            "content",
            "response",
        ):
            if key in value:
                candidate = value[key]

                if isinstance(candidate, list):
                    for elem in candidate:
                        found = unpack_matrix(elem)
                        if found:
                            return found
                    return None

                value = candidate
                break
        else:
            return None

    return None


def chart_html(matrix: dict, tool_result: dict) -> Optional[str]:
    raw_series = matrix.get("result", [])
    series = []

    for raw in raw_series[:10]:
        if not isinstance(raw, dict):
            continue

        points = []

        for x in raw.get("values", [])[:2000]:
            try:
                ts, val = float(x[0]), float(x[1])

                if val == val and abs(val) != float("inf"):
                    points.append([int(ts * 1000), val])

            except (ValueError, TypeError, IndexError):
                pass

        if points:
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
    ).replace("<", "\\u003c").replace("</", "<\\/")

    return '''
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
    font: 14px system-ui, -apple-system, "Segoe UI", sans-serif;
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
    <button data-mode="line" class="active">
        Line 折線
    </button>

    <button data-mode="area">
        Area 面積
    </button>

    <button data-mode="bars">
        Bars 柱狀
    </button>

    <button data-mode="points">
        Points 散點
    </button>

    <button id="axis">
        Y 軸：自動
    </button>
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

document.getElementById('stats').innerHTML =
    S.slice(0, 4).map((s, i) => {
        let v = s.points.map(p => p[1]);
        let sum = v.reduce((a, b) => a + b, 0);

        let safeName = s.name.replace(
            /[&<>"']/g,
            c => ({
                '&': '&amp;',
                '<': '&lt;',
                '>': '&gt;',
                '"': '&quot;',
                "'": '&#39;'
            }[c])
        );

        return `
        <div class="stat">
            <small>${safeName}</small>
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
    const hi = ymax;

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
        let y = t + ph * i / 4;

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
        let x = l + pw * i / 4;

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
            let bw = Math.max(
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
            x.classList.toggle('active', x === b);
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

    if (
        x < layout.l ||
        x > layout.w - layout.r
    ) {
        tip.style.display = 'none';
        return;
    }

    let ts =
        xmin +
        (x - layout.l) /
        layout.pw *
        (xmax - xmin);

    let nearest = null;
    let dist = Infinity;

    S.forEach((s, i) => {
        s.points.forEach(p => {
            let d = Math.abs(p[0] - ts);

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
        '\n' +
        date(nearest.p[0]) +
        '\n' +
        fmt(nearest.p[1]);

    tip.style.display = 'block';

    tip.style.left =
        Math.min(x + 10, layout.w - 155) + 'px';

    tip.style.top = '20px';
});

canvas.addEventListener('mouseleave', () => {
    tip.style.display = 'none';
});

new ResizeObserver(draw).observe(
    canvas.parentElement
);

draw();
</script>
</body>
</html>
'''.replace("__DATA__", payload)


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

        MAX_CHARTS: int = Field(
            default=2,
            ge=0,
            le=5,
        )

    def __init__(self):
        self.type = "pipe"
        self.id = "holmesgpt_sre_v530"
        self.name = "HolmesGPT SRE 5.3.0"
        self.valves = self.Valves()

    def pipes(self):
        return [
            {
                "id": "sre",
                "name": "HolmesGPT SRE 5.3.0",
            }
        ]

    async def pipe(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __event_emitter__=None,
    ):

        async def emit(event_type: str, data: dict):
            if __event_emitter__:
                await __event_emitter__({
                    "type": event_type,
                    "data": data,
                })

        async def status(
            description: str,
            done=False,
        ):
            await emit(
                "status",
                {
                    "description": description,
                    "done": done,
                },
            )

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

        messages = body.get("messages") or []

        question = ""

        for msg in reversed(messages):
            if msg.get("role") == "user":
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

        # 只有使用者要求圖表時才顯示
        show_chart = chart_requested(question)

        await status(
            "HolmesGPT 正在開始調查..."
        )

        started = time.perf_counter()

        tool_runs = {}
        tool_order = []

        final_answer = ""
        intermediary = []
        error_text = ""

        finished = False
        charts = 0

        sse_event = ""
        sse_data = []

        async def handle(
            event_name: str,
            payload: dict,
        ):
            nonlocal final_answer
            nonlocal error_text
            nonlocal finished
            nonlocal charts

            if not isinstance(payload, dict):
                return

            name = (
                event_name
                or str(
                    payload.get("event")
                    or payload.get("type")
                    or ""
                )
            )

            # ----------------------------
            # Tool start
            # ----------------------------
            if name == "start_tool_calling":

                key = str(
                    payload.get("id")
                    or payload.get("tool_call_id")
                    or f"unknown-{len(tool_order) + 1}"
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
                }

                tool_order.append(key)

                await status(
                    f"正在執行 {tool}..."
                )

            # ----------------------------
            # Tool result
            # ----------------------------
            elif name == "tool_calling_result":

                key = str(
                    payload.get("tool_call_id")
                    or payload.get("id")
                    or ""
                )

                result = (
                    payload.get("result")
                    or {}
                )

                if not isinstance(result, dict):
                    result = {
                        "data": result
                    }

                tool = str(
                    payload.get("name")
                    or payload.get("tool_name")
                    or tool_runs.get(
                        key, {}
                    ).get("name")
                    or "tool"
                )

                if key not in tool_runs:
                    key = (
                        key
                        or f"result-{len(tool_order) + 1}"
                    )

                    tool_runs[key] = {
                        "name": tool,
                        "start": None,
                        "status": "running",
                        "seconds": None,
                    }

                    tool_order.append(key)

                item = tool_runs[key]

                item["name"] = tool

                item["status"] = result.get(
                    "status",
                    "unknown",
                )

                item["seconds"] = (
                    round(
                        time.perf_counter()
                        - item["start"],
                        2,
                    )
                    if item["start"]
                    else None
                )

                await status(
                    f"{tool} 執行"
                    + (
                        "成功"
                        if item["status"] == "success"
                        else (
                            "結束："
                            + str(item["status"])
                        )
                    )
                )

                # ------------------------
                # Prometheus chart
                # Only when requested
                # ------------------------
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
                            await emit(
                                "embeds",
                                {
                                    "embeds": [
                                        markup
                                    ]
                                },
                            )

                            charts += 1

            # ----------------------------
            # AI intermediate message
            # ----------------------------
            elif name == "ai_message":

                content = payload.get(
                    "content"
                )

                if (
                    isinstance(content, str)
                    and content.strip()
                ):
                    intermediary.append(
                        content.strip()
                    )

                await status(
                    "AI 正在分析調查結果..."
                )

            # ----------------------------
            # Final answer
            # ----------------------------
            elif name == "ai_answer_end":

                finished = True

                ans = (
                    payload.get("analysis")
                    or payload.get("answer")
                    or payload.get("content")
                    or ""
                )

                if isinstance(ans, str):
                    final_answer = ans.strip()

            # ----------------------------
            # Error events
            # ----------------------------
            elif name in (
                "error",
                "stream_error",
                "ai_error",
            ):

                err = (
                    payload.get("error")
                    or payload.get("message")
                    or payload.get("detail")
                    or payload
                )

                error_text = (
                    err
                    if isinstance(err, str)
                    else json.dumps(
                        err,
                        ensure_ascii=False,
                    )[:1000]
                )

        # ----------------------------
        # SSE parser
        # ----------------------------
        async def flush_sse():
            nonlocal sse_event
            nonlocal sse_data

            if sse_data:

                text = "\n".join(
                    sse_data
                )

                try:
                    payload = json.loads(
                        text
                    )

                except ValueError:
                    payload = {
                        "message": text
                    }

                await handle(
                    sse_event,
                    payload,
                )

            sse_event = ""
            sse_data = []

        # ----------------------------
        # HolmesGPT request
        # ----------------------------
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

                    response.raise_for_status()

                    async for line in response.aiter_lines():

                        if line == "":
                            await flush_sse()

                        elif line.startswith("event:"):
                            sse_event = (
                                line[6:].strip()
                            )

                        elif line.startswith("data:"):
                            sse_data.append(
                                line[5:].lstrip()
                            )

                    await flush_sse()

        except httpx.HTTPStatusError as exc:

            error_text = (
                f"HolmesGPT HTTP "
                f"{exc.response.status_code}: "
                f"{exc.response.text[:500]}"
            )

        except httpx.TimeoutException:

            error_text = (
                f"HolmesGPT 超過 "
                f"{self.valves.TIMEOUT_SECONDS} "
                f"秒未完成傳輸"
            )

        except Exception as exc:

            error_text = (
                f"{type(exc).__name__}: "
                f"{str(exc)[:600]}"
            )

        elapsed = (
            time.perf_counter()
            - started
        )

        await status(
            f"調查結束，耗時 {elapsed:.2f} 秒",
            done=True,
        )

        # ----------------------------
        # Final report
        # ----------------------------
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
                "請查看 HolmesGPT Pod 日誌"
                "與 vLLM 連線狀態。"
            )

        else:

            report.append(
                "**調查未完成**："
                "收到 `ai_answer_end`，"
                "但 `analysis` 為空。"
                "請檢查 Qwen 最終回答"
                "與輸出 Token 限制。"
            )

        # ----------------------------
        # Tool execution report
        # ----------------------------
        if self.valves.SHOW_TOOL_REPORT:

            report.append(
                "\n**工具執行紀錄**"
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

                symbol = (
                    "✓"
                    if item["status"] == "success"
                    else "✗"
                )

                report.append(
                    f"- {symbol} "
                    f"`{item['name']}`："
                    f"{item['status']}"
                    f"{duration}"
                )

            report.append(
                f"工具 {len(tool_order)} 次；"
                f"互動圖表 {charts} 張"
                f"（{'使用者要求' if show_chart else '未要求，不顯示'}）。"
            )

        return "\n\n".join(report)
