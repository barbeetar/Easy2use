
"""
title: HolmesGPT SRE
description: Kubernetes SRE Agent with Grafana PNG and Prometheus trend charts
version: 5.0.0
"""

import base64
import html
import json
import math
import re

from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field


class Pipe:

    class Valves(BaseModel):

        HOLMES_URL: str = Field(
            default="http://holmesgpt-holmes.holmesgpt.svc.cluster.local:80"
        )

        MODEL: str = Field(default="gemma-local")

        API_KEY: str = Field(
            default="",
            json_schema_extra={"input": {"type": "password"}},
        )

        TIMEOUT: int = Field(default=300)

        SHOW_TOOL_HISTORY: bool = Field(default=True)

        GRAFANA_URL: str = Field(
            default="http://grafana.monitoring.svc.cluster.local:80"
        )

        GRAFANA_TOKEN: str = Field(
            default="",
            json_schema_extra={"input": {"type": "password"}},
        )

        GRAFANA_ORG_ID: int = Field(default=1)

        SHOW_RENDERED_IMAGES: bool = Field(default=True)

        SHOW_PROMETHEUS_CHARTS: bool = Field(default=True)

        MAX_IMAGES: int = Field(default=2, ge=1, le=4)

        MAX_CHARTS: int = Field(default=3, ge=1, le=5)

        MAX_IMAGE_BYTES: int = Field(default=1500000)

        REMOVE_TOOL_IMAGE_REFERENCES: bool = Field(
            default=True
        )

    def __init__(self):
        self.name = "HolmesGPT SRE"
        self.valves = self.Valves()

    # =========================================================
    # Helpers
    # =========================================================

    @staticmethod
    def clean_images(text):

        text = str(text or "")

        return re.sub(
            r"!\[([^\]]*)\]\(\s*tool-image://[^)\s]+\s*\)",
            lambda m: f"\n\n*{m.group(1)}*\n\n",
            text,
            flags=re.IGNORECASE,
        ).strip()

    @staticmethod
    def decode_prometheus_result(result):

        data = result.get("data")

        # HolmesGPT 0.46.0 returns Prometheus output
        # as a JSON-encoded string.

        for _ in range(3):

            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except json.JSONDecodeError:
                    return None

            if not isinstance(data, dict):
                return None

            if (
                data.get("status") == "error"
                or data.get("error_message")
            ):
                return None

            if isinstance(data.get("data"), dict):
                data = data["data"]
                continue

            break

        if not isinstance(data, dict):
            return None

        if data.get("resultType") != "matrix":
            return None

        if not isinstance(data.get("result"), list):
            return None

        return data

    @staticmethod
    def chart_unit(output_type):

        t = str(output_type or "").lower()

        if "memory" in t or "byte" in t:
            return "MiB", 1024 * 1024

        if "cpu" in t:
            return "CPU cores", 1

        if "percent" in t or "percentage" in t:
            return "%", 1

        return "Value", 1

    @staticmethod
    def chart_html(job):

        params = job.get("params") or {}
        matrix = job.get("matrix") or {}

        unit, divisor = Pipe.chart_unit(
            params.get("output_type")
        )

        series = []

        for item in matrix.get("result", [])[:4]:

            if not isinstance(item, dict):
                continue

            metric = item.get("metric") or {}
            values = item.get("values") or []

            points = []

            for pair in values[:240]:

                try:
                    timestamp = float(pair[0])
                    value = float(pair[1]) / divisor

                    if not (
                        math.isfinite(timestamp)
                        and math.isfinite(value)
                    ):
                        continue

                    points.append((timestamp, value))

                except (TypeError, ValueError, IndexError):
                    continue

            if not points:
                continue

            name = (
                metric.get("pod")
                or metric.get("container")
                or metric.get("instance")
                or metric.get("job")
                or "Series"
            )

            series.append({
                "name": str(name),
                "points": points,
            })

        if not series:
            return None

        all_points = [
            point
            for s in series
            for point in s["points"]
        ]

        min_x = min(p[0] for p in all_points)
        max_x = max(p[0] for p in all_points)

        min_y = min(p[1] for p in all_points)
        max_y = max(p[1] for p in all_points)

        if min_x == max_x:
            max_x = min_x + 1

        # Include zero for nonnegative CPU / Memory values.
        if min_y >= 0:
            min_y = 0

        if min_y == max_y:
            max_y = min_y + 1

        padding_y = (max_y - min_y) * 0.08
        max_y += padding_y

        width = 1000
        height = 400

        left = 76
        right = 30
        top = 24
        bottom = 62

        plot_width = width - left - right
        plot_height = height - top - bottom

        def sx(t):
            return (
                left
                + (t - min_x)
                / (max_x - min_x)
                * plot_width
            )

        def sy(v):
            return (
                top
                + (max_y - v)
                / (max_y - min_y)
                * plot_height
            )

        colors = [
            "#38bdf8",
            "#34d399",
            "#fbbf24",
            "#c084fc",
        ]

        svg_parts = []

        # Y-axis grid
        for i in range(5):

            value = min_y + (max_y - min_y) * i / 4
            y = sy(value)

            svg_parts.append(
                f'<line x1="{left}" y1="{y:.2f}" '
                f'x2="{width-right}" y2="{y:.2f}" '
                'stroke="#334155" stroke-width="1" '
                'stroke-dasharray="4 5"/>'
            )

            svg_parts.append(
                f'<text x="{left-12}" y="{y+4:.2f}" '
                'text-anchor="end" '
                'fill="#cbd5e1" font-size="14">'
                f'{value:.2f}</text>'
            )

        tw_tz = timezone(timedelta(hours=8))

        # X-axis labels
        for i in range(5):

            t = min_x + (max_x - min_x) * i / 4
            x = sx(t)

            label = datetime.fromtimestamp(
                t,
                tz=tw_tz,
            ).strftime("%H:%M")

            svg_parts.append(
                f'<text x="{x:.2f}" y="{height-30}" '
                'text-anchor="middle" '
                'fill="#cbd5e1" font-size="14">'
                f'{label}</text>'
            )

        # Data series
        for idx, s in enumerate(series):

            color = colors[idx]

            coords = " ".join(
                f"{sx(t):.2f},{sy(v):.2f}"
                for t, v in s["points"]
            )

            svg_parts.append(
                f'<polyline points="{coords}" '
                'fill="none" '
                f'stroke="{color}" '
                'stroke-width="2.6" '
                'stroke-linecap="round" '
                'stroke-linejoin="round"/>'
            )

        # Legend
        legend_items = []

        for idx, s in enumerate(series):

            color = colors[idx]
            name = html.escape(s["name"])

            legend_items.append(
                '<div class="legend-item">'
                f'<span class="dot" style="background:{color}"></span>'
                f'<span>{name}</span>'
                '</div>'
            )

        query = html.escape(
            str(params.get("query") or "")
        )

        description = html.escape(
            str(
                params.get("description")
                or "Prometheus Trend"
            )
        )

        unit_safe = html.escape(unit)

        total_points = sum(
            len(s["points"]) for s in series
        )

        svg = "".join(svg_parts)
        legends = "".join(legend_items)

        return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
* {{ box-sizing:border-box; }}
html,body {{
    margin:0;
    padding:0;
    width:100%;
    overflow:hidden;
    background:transparent;
    color:#e2e8f0;
    font-family:system-ui,sans-serif;
}}
.card {{
    background:#0f172a;
    border:1px solid #334155;
    border-radius:12px;
    padding:16px;
    width:100%;
}}
.title {{
    font-weight:600;
    font-size:16px;
    margin-bottom:5px;
    overflow-wrap:anywhere;
}}
.subtitle {{
    color:#94a3b8;
    font-size:12px;
    margin-bottom:12px;
}}
svg {{
    display:block;
    width:100%;
    height:auto;
}}
.legend {{
    display:flex;
    flex-wrap:wrap;
    gap:12px;
    margin-top:10px;
}}
.legend-item {{
    display:flex;
    align-items:center;
    gap:6px;
    font-size:12px;
    overflow-wrap:anywhere;
}}
.dot {{
    width:10px;
    height:10px;
    border-radius:50%;
    flex-shrink:0;
}}
.details {{
    margin-top:12px;
    font-size:12px;
    color:#94a3b8;
}}
code {{
    white-space:pre-wrap;
    overflow-wrap:anywhere;
    color:#cbd5e1;
}}
</style>
</head>
<body>
<div class="card">
  <div class="title">{description}</div>
  <div class="subtitle">
    單位：{unit_safe} · 台灣時間 UTC+8 · {total_points} 個資料點
  </div>
  <svg viewBox="0 0 {width} {height}"
       xmlns="http://www.w3.org/2000/svg"
       role="img"
       aria-label="{description}">
    {svg}
  </svg>
  <div class="legend">{legends}</div>
  <details class="details">
    <summary>查看 PromQL</summary>
    <code>{query}</code>
  </details>
</div>
<script>
(function() {{
    function reportHeight() {{
        var h = Math.ceil(
            document.documentElement.scrollHeight
        );
        parent.postMessage(
            {{type:"iframe:height",height:h}},
            "*"
        );
    }}
    window.addEventListener("load", reportHeight);
    window.addEventListener("resize", reportHeight);
    if (typeof ResizeObserver !== "undefined") {{
        new ResizeObserver(reportHeight).observe(
            document.body
        );
    }}
    reportHeight();
}})();
</script>
</body>
</html>"""

    # =========================================================
    # Grafana
    # =========================================================

    @staticmethod
    def safe_time(value, fallback):

        value = str(value or fallback)

        if re.fullmatch(
            r"(?:now(?:-[0-9]{1,4}[smhdw])?|[0-9]{13})",
            value,
        ):
            return value

        return fallback

    def grafana_request(self, job):

        params = job["params"]
        tool = job["tool"]

        uid = str(
            params.get("dashboard_uid") or ""
        )

        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", uid):
            raise ValueError("Invalid Dashboard UID")

        if tool == "grafana_render_panel":

            panel_id = int(params.get("panel_id") or 0)

            if not 1 <= panel_id <= 100000:
                raise ValueError("Invalid panel_id")

            path = f"render/d-solo/{quote(uid, safe='')}/_"
            title = f"Grafana Panel {panel_id}"

        else:
            panel_id = None
            path = f"render/d/{quote(uid, safe='')}/_"
            title = "Grafana Dashboard"

        query = {
            "orgId": self.valves.GRAFANA_ORG_ID,
            "from": self.safe_time(
                params.get("from_time"), "now-1h"
            ),
            "to": self.safe_time(
                params.get("to_time"), "now"
            ),
            "width": min(
                max(int(params.get("width") or 1000), 400),
                1400,
            ),
            "height": min(
                max(int(params.get("height") or 500), 250),
                900,
            ),
            "theme": (
                "light"
                if params.get("theme") == "light"
                else "dark"
            ),
        }

        if panel_id is not None:
            query["panelId"] = panel_id

        tz = str(params.get("timezone") or "")

        if re.fullmatch(r"[A-Za-z0-9_+./-]{1,64}", tz):
            query["tz"] = tz

        variables = params.get("variables")

        if isinstance(variables, str):

            for pair in variables.split(";")[:12]:

                if "=" not in pair:
                    continue

                key, val = (
                    x.strip()
                    for x in pair.split("=", 1)
                )

                if re.fullmatch(
                    r"var-[A-Za-z0-9_-]{1,64}", key
                ) and re.fullmatch(
                    r"[A-Za-z0-9_.:/ -]{1,128}", val
                ):
                    query[key] = val

        return title, path, query

    @staticmethod
    def grafana_html(title, image_b64):

        safe_title = html.escape(title, quote=True)

        return (
            '<!doctype html><html><head>'
            '<meta charset="utf-8">'
            '<meta name="viewport" '
            'content="width=device-width,initial-scale=1">'
            '<style>'
            '*{box-sizing:border-box}'
            'html,body{margin:0;padding:0;'
            'width:100%;overflow:hidden;'
            'background:transparent}'
            'img{display:block;width:100%;'
            'height:auto;max-width:100%}'
            '</style></head><body>'
            f'<img id="grafana" alt="{safe_title}" '
            f'src="data:image/png;base64,{image_b64}">'
            '<script>'
            '(function(){'
            'function resize(){'
            'parent.postMessage({type:"iframe:height",'
            'height:Math.ceil('
            'document.documentElement.scrollHeight'
            ')},"*")}'
            'window.addEventListener("load",resize);'
            'window.addEventListener("resize",resize);'
            'var img=document.getElementById("grafana");'
            'if(img)img.addEventListener("load",resize);'
            'if(typeof ResizeObserver!=="undefined")'
            'new ResizeObserver(resize).observe(document.body);'
            'resize()'
            '})();'
            '</script></body></html>'
        )

    # =========================================================
    # Main
    # =========================================================

    async def pipe(
        self,
        body: dict,
        __user__: dict = None,
        __event_emitter__=None,
        __task__=None,
    ):

        # Keep original admin-only restriction.

        if not __user__ or __user__.get("role") != "admin":
            return "Access denied: administrator access required."

        if __task__:
            return "HolmesGPT SRE"

        messages = body.get("messages", [])

        questions = [
            m["content"]
            for m in messages
            if m.get("role") == "user"
            and isinstance(m.get("content"), str)
        ]

        if not questions:
            return "請輸入 Kubernetes 維運問題。"

        question = questions[-1].strip()

        if not question:
            return "請輸入 Kubernetes 維運問題。"

        system_prompt = "\n\n".join(
            m["content"]
            for m in messages
            if m.get("role") == "system"
            and isinstance(m.get("content"), str)
        )

        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

        if self.valves.API_KEY:
            headers["X-API-Key"] = self.valves.API_KEY

        payload = {
            "model": self.valves.MODEL,
            "ask": question,
            "stream": True,
            "enable_tool_approval": False,
            "additional_system_prompt": system_prompt,
        }

        holmes_url = (
            self.valves.HOLMES_URL.rstrip("/")
            + "/api/chat"
        )

        tool_history = []
        tool_params_by_id = {}

        grafana_jobs = []
        prometheus_jobs = {}

        seen_grafana = set()

        final_answer = None
        pending_event = ""
        pending_data = []

        async def status(message, done=False):

            if __event_emitter__:
                await __event_emitter__({
                    "type": "status",
                    "data": {
                        "description": message,
                        "done": done,
                        "hidden": False,
                    },
                })

        async def handle_event(event_type, data):

            nonlocal final_answer

            if event_type == "start_tool_calling":

                tool = (
                    data.get("tool_name")
                    or data.get("name")
                    or "unknown"
                )

                call_id = (
                    data.get("id")
                    or data.get("tool_call_id")
                )

                params = (
                    data.get("params")
                    or data.get("parameters")
                    or {}
                )

                if call_id and isinstance(params, dict):
                    tool_params_by_id[call_id] = params

                tool_history.append({
                    "tool": tool,
                    "status": "running",
                    "id": call_id,
                    "message": "執行中",
                })

                await status(f"正在執行 {tool}...")

            elif event_type == "tool_calling_result":

                tool = (
                    data.get("name")
                    or data.get("tool_name")
                    or "unknown"
                )

                call_id = (
                    data.get("tool_call_id")
                    or data.get("id")
                )

                result = data.get("result") or {}

                if not isinstance(result, dict):
                    result = {}

                params = (
                    result.get("params")
                    or data.get("params")
                    or tool_params_by_id.get(call_id)
                    or {}
                )

                if not isinstance(params, dict):
                    params = {}

                outcome = str(
                    result.get("status") or "unknown"
                ).lower()

                error = result.get("error")

                # Some HolmesGPT Kubernetes tools place
                # subprocess errors inside data.stderr
                # even when status is "success".

                stderr = ""

                raw_data = result.get("data")

                if isinstance(raw_data, dict):
                    stderr = str(
                        raw_data.get("stderr") or ""
                    )

                elif isinstance(raw_data, str):

                    try:
                        parsed = json.loads(raw_data)

                        if isinstance(parsed, dict):
                            stderr = str(
                                parsed.get("stderr") or ""
                            )
                    except json.JSONDecodeError:
                        pass

                if re.search(
                    r"runtime: out of memory|"
                    r"fatal error: out of memory|"
                    r"Unable to find resource kind",
                    stderr,
                    flags=re.IGNORECASE,
                ):
                    outcome = "error"
                    error = stderr.splitlines()[0]

                if outcome == "success":
                    message = f"{tool} 查詢完成"
                else:
                    message = (
                        f"{tool} 失敗："
                        f"{str(error or stderr or 'unknown')[:150]}"
                    )

                matched = False

                for item in reversed(tool_history):

                    if item["status"] != "running":
                        continue

                    if (
                        (call_id and item["id"] == call_id)
                        or item["tool"] == tool
                    ):

                        item.update(
                            status=outcome,
                            message=message,
                        )
                        matched = True
                        break

                if not matched:
                    tool_history.append({
                        "tool": tool,
                        "status": outcome,
                        "id": call_id,
                        "message": message,
                    })

                # ---------------------------------------------
                # Prometheus range query:
                # Store only validated matrix data.
                # ---------------------------------------------

                if (
                    self.valves.SHOW_PROMETHEUS_CHARTS
                    and outcome == "success"
                    and tool == "execute_prometheus_range_query"
                    and call_id
                ):

                    matrix = self.decode_prometheus_result(
                        result
                    )

                    if (
                        matrix
                        and len(prometheus_jobs)
                        < self.valves.MAX_CHARTS
                    ):

                        prometheus_jobs[call_id] = {
                            "params": dict(params),
                            "matrix": matrix,
                        }

                # ---------------------------------------------
                # Grafana rendering jobs
                # ---------------------------------------------

                if (
                    self.valves.SHOW_RENDERED_IMAGES
                    and outcome == "success"
                    and tool in {
                        "grafana_render_panel",
                        "grafana_render_dashboard",
                    }
                ):

                    key = json.dumps(
                        {
                            "tool": tool,
                            "params": params,
                        },
                        sort_keys=True,
                        ensure_ascii=False,
                    )

                    if (
                        key not in seen_grafana
                        and len(grafana_jobs)
                        < self.valves.MAX_IMAGES
                    ):
                        seen_grafana.add(key)
                        grafana_jobs.append({
                            "tool": tool,
                            "params": dict(params),
                        })

                await status(message)

            elif event_type == "ai_message":

                if data.get("content"):
                    await status("AI 正在分析調查結果...")

            elif event_type == "ai_answer_end":

                final_answer = (
                    data.get("analysis")
                    or data.get("answer")
                    or ""
                )

                await status("分析完成")

            elif event_type == "error":

                await status(
                    "HolmesGPT 回傳錯誤："
                    + str(
                        data.get("message")
                        or data.get("error")
                        or "unknown"
                    )[:140]
                )

        async def process_sse():

            nonlocal pending_event, pending_data

            if not pending_data:
                pending_event = ""
                return

            raw = "\n".join(pending_data)
            event_type = pending_event or "message"

            pending_event = ""
            pending_data = []

            if raw == "[DONE]":
                return

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                return

            if isinstance(data, dict):
                await handle_event(event_type, data)

        try:

            await status("HolmesGPT 正在開始調查")

            timeout = httpx.Timeout(
                timeout=float(self.valves.TIMEOUT),
                connect=10.0,
            )

            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
            ) as client:

                async with client.stream(
                    "POST",
                    holmes_url,
                    headers=headers,
                    json=payload,
                ) as response:

                    response.raise_for_status()

                    async for line in response.aiter_lines():

                        if not line.strip():
                            await process_sse()
                            continue

                        if line.startswith(":"):
                            continue

                        if line.startswith("event:"):
                            pending_event = line[6:].strip()

                        elif line.startswith("data:"):
                            pending_data.append(
                                line[5:].lstrip()
                            )

                    await process_sse()

            if not final_answer:

                await status(
                    "HolmesGPT 未回傳最終回答",
                    done=True,
                )

                return "HolmesGPT 未回傳完整調查結果。"

            output = str(final_answer)

            if self.valves.REMOVE_TOOL_IMAGE_REFERENCES:
                output = self.clean_images(output)

            # =================================================
            # Render PromQL markers
            # =================================================

            promql_pattern = re.compile(
                r"<<\s*(\{.*?\})\s*>>",
                flags=re.DOTALL,
            )

            referenced_ids = []

            def replace_promql_marker(match):

                try:
                    marker = json.loads(match.group(1))
                except json.JSONDecodeError:
                    return match.group(0)

                if marker.get("type") != "promql":
                    return match.group(0)

                call_id = marker.get("tool_call_id")

                if call_id in prometheus_jobs:
                    referenced_ids.append(call_id)
                    return "（Prometheus 趨勢圖附於下方）"

                return (
                    "（未取得可繪製的 Prometheus "
                    "時間序列資料）"
                )

            output = promql_pattern.sub(
                replace_promql_marker,
                output,
            )

            # =================================================
            # Emit Prometheus chart embeds
            # =================================================

            shown_charts = 0

            if (
                self.valves.SHOW_PROMETHEUS_CHARTS
                and __event_emitter__
            ):

                chart_ids = list(dict.fromkeys(
                    referenced_ids
                    + list(prometheus_jobs.keys())
                ))

                for call_id in chart_ids[
                    :self.valves.MAX_CHARTS
                ]:

                    job = prometheus_jobs.get(call_id)

                    if not job:
                        continue

                    chart = self.chart_html(job)

                    if not chart:
                        continue

                    await __event_emitter__({
                        "type": "embeds",
                        "data": {
                            "embeds": [chart],
                        },
                    })

                    shown_charts += 1

            # =================================================
            # Render Grafana PNG
            # =================================================

            shown_grafana = 0

            if (
                grafana_jobs
                and __event_emitter__
                and self.valves.GRAFANA_TOKEN
            ):

                grafana_headers = {
                    "Authorization": (
                        "Bearer " + self.valves.GRAFANA_TOKEN
                    ),
                    "X-Grafana-Org-Id": str(
                        self.valves.GRAFANA_ORG_ID
                    ),
                    "Accept": "image/png",
                }

                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(
                        90.0,
                        connect=10.0,
                    ),
                    follow_redirects=False,
                ) as grafana_client:

                    for job in grafana_jobs:

                        try:

                            title, path, query = (
                                self.grafana_request(job)
                            )

                            url = (
                                self.valves.GRAFANA_URL.rstrip("/")
                                + "/"
                                + path
                            )

                            response = await grafana_client.get(
                                url,
                                params=query,
                                headers=grafana_headers,
                            )

                            response.raise_for_status()

                            png = response.content

                            if not png.startswith(
                                b"\x89PNG\r\n\x1a\n"
                            ):
                                raise ValueError("Invalid PNG")

                            if (
                                len(png)
                                > self.valves.MAX_IMAGE_BYTES
                            ):
                                raise ValueError("PNG too large")

                            image_b64 = base64.b64encode(
                                png
                            ).decode("ascii")

                            embed = self.grafana_html(
                                title,
                                image_b64,
                            )

                            await __event_emitter__({
                                "type": "embeds",
                                "data": {
                                    "embeds": [embed],
                                },
                            })

                            shown_grafana += 1

                        except (
                            httpx.HTTPError,
                            ValueError,
                            TypeError,
                            OverflowError,
                        ):
                            continue

            # =================================================
            # Tool history
            # =================================================

            if self.valves.SHOW_TOOL_HISTORY and tool_history:

                lines = [
                    "",
                    "---",
                    "### 工具執行紀錄",
                ]

                for i, item in enumerate(
                    tool_history[:30], 1
                ):

                    symbol = (
                        "✓"
                        if item["status"] == "success"
                        else "!"
                    )

                    lines.append(
                        f"{i}. {symbol} "
                        f"`{item['tool']}` — "
                        f"{item['message']}"
                    )

                output += "\n".join(lines)

            if shown_charts:
                output += (
                    f"\n\n已附上 {shown_charts} 張 "
                    "Prometheus 趨勢圖。"
                )

            if shown_grafana:
                output += (
                    f"\n\n已附上 {shown_grafana} 張 "
                    "Grafana 渲染圖片。"
                )

            await status(
                "Kubernetes 調查完成",
                done=True,
            )

            return output

        except httpx.TimeoutException:

            await status("HolmesGPT 調查逾時", done=True)
            return "HolmesGPT 調查逾時。"

        except httpx.HTTPStatusError as exc:

            await status("HolmesGPT API 錯誤", done=True)
            return (
                "HolmesGPT HTTP 錯誤："
                + str(exc.response.status_code)
            )

        except httpx.RequestError:

            await status("HolmesGPT 連線失敗", done=True)
            return "無法連線 HolmesGPT API。"

        except Exception as exc:

            await status("Pipe 執行失敗", done=True)
            return (
                "Pipe 處理錯誤："
                + type(exc).__name__
            )
