
"""
title: HolmesGPT SRE
description: HolmesGPT Kubernetes investigation with live tool progress and Grafana render embeds
version: 2.3.0
"""

import base64
import html
import json
import re
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field


class Pipe:
    class Valves(BaseModel):
        HOLMES_URL: str = Field(
            default="http://holmesgpt-holmes.holmesgpt.svc.cluster.local:80",
            description="HolmesGPT API URL",
        )

        MODEL: str = Field(
            default="gemma-local",
            description="HolmesGPT model alias",
        )

        API_KEY: str = Field(
            default="",
            description="HolmesGPT API key, if enabled",
            json_schema_extra={"input": {"type": "password"}},
        )

        TIMEOUT: int = Field(
            default=300,
            description="Investigation timeout in seconds",
        )

        SHOW_TOOL_HISTORY: bool = Field(
            default=True,
            description="Include tool execution summary",
        )

        GRAFANA_URL: str = Field(
            default="http://grafana.monitoring.svc.cluster.local:80",
            description="Internal Grafana URL reachable from Open WebUI",
        )

        GRAFANA_TOKEN: str = Field(
            default="",
            description="Read-only Grafana service account token",
            json_schema_extra={"input": {"type": "password"}},
        )

        GRAFANA_ORG_ID: int = Field(
            default=1,
            description="Grafana organization ID",
        )

        SHOW_RENDERED_IMAGES: bool = Field(
            default=True,
            description="Show successful Grafana render tool results",
        )

        MAX_IMAGES: int = Field(
            default=2,
            ge=1,
            le=4,
            description="Max rendered images per response",
        )

        MAX_IMAGE_BYTES: int = Field(
            default=1500000,
            ge=100000,
            le=3000000,
            description="Maximum PNG bytes per image",
        )

    def __init__(self):
        self.name = "HolmesGPT SRE"
        self.valves = self.Valves()

    async def pipe(
        self,
        body: dict,
        __user__: dict = None,
        __event_emitter__=None,
        __task__=None,
    ):
        # ---------------------------------------------------------
        # Security: administrator access only
        # ---------------------------------------------------------

        if not __user__ or __user__.get("role") != "admin":
            return "Access denied: administrator access required."

        if __task__:
            return "HolmesGPT SRE"

        # ---------------------------------------------------------
        # Read user question and system prompt
        # ---------------------------------------------------------

        questions = [
            m.get("content")
            for m in body.get("messages", [])
            if m.get("role") == "user"
            and isinstance(m.get("content"), str)
        ]

        if not questions or not questions[-1].strip():
            return "請輸入 Kubernetes 維運問題。"

        question = questions[-1].strip()

        system_prompt = "\n\n".join(
            str(m.get("content", ""))
            for m in body.get("messages", [])
            if m.get("role") == "system"
            and isinstance(m.get("content"), str)
        )

        # ---------------------------------------------------------
        # HolmesGPT request
        # ---------------------------------------------------------

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

        # ---------------------------------------------------------
        # Investigation state
        # ---------------------------------------------------------

        tool_history = []
        tool_params_by_id = {}
        render_jobs = []

        final_answer = None
        pending_event = ""
        pending_data = []
        completed = False

        # ---------------------------------------------------------
        # Open WebUI status
        # ---------------------------------------------------------

        async def status(message, done=False):
            if __event_emitter__:
                await __event_emitter__(
                    {
                        "type": "status",
                        "data": {
                            "description": message,
                            "done": done,
                            "hidden": False,
                        },
                    }
                )

        # ---------------------------------------------------------
        # Parse structured tool results
        # ---------------------------------------------------------

        def get_result_objects(raw):
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except (ValueError, TypeError):
                    return None

            if isinstance(raw, list):
                return raw

            if isinstance(raw, dict):
                if isinstance(raw.get("items"), list):
                    return raw["items"]

                if isinstance(raw.get("results"), list):
                    return raw["results"]

            return None

        # ---------------------------------------------------------
        # Describe tool progress
        # ---------------------------------------------------------

        def describe_tool(tool, result, description, params):
            outcome = str(
                result.get("status") or "unknown"
            ).lower()

            if outcome != "success":
                error = str(
                    result.get("error") or "工具執行未成功"
                )[:140]

                return f"{tool} 失敗：{error}"

            if tool in {
                "grafana_render_panel",
                "grafana_render_dashboard",
            }:
                return f"Grafana 圖片渲染成功：{tool}"

            if tool == "fetch_skill":
                return "已取得調查 Skill"

            if (
                tool.startswith("grafana")
                or tool.startswith("query_prometheus")
                or tool.startswith("execute_prometheus")
            ):
                return f"已完成監控查詢：{tool}"

            objects = get_result_objects(
                result.get("data")
            )

            namespace = (
                params.get("namespace")
                or params.get("ns")
                or ""
            )

            if objects is not None and tool.startswith("kubernetes"):
                location = (
                    f"{namespace} Namespace"
                    if namespace
                    else "Kubernetes"
                )

                return (
                    f"已完成 {location} 查詢，"
                    f"回傳 {len(objects)} 筆精簡資料"
                )

            return f"已完成工具查詢：{tool}"

        # ---------------------------------------------------------
        # HolmesGPT SSE event handler
        # ---------------------------------------------------------

        async def handle_event(event_type, data):
            nonlocal final_answer, completed

            # Tool started
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

                start_params = (
                    data.get("params")
                    or data.get("parameters")
                    or {}
                )

                if call_id and isinstance(start_params, dict):
                    tool_params_by_id[call_id] = start_params

                tool_history.append(
                    {
                        "tool": tool,
                        "status": "running",
                        "id": call_id,
                        "message": "執行中",
                    }
                )

                await status(f"正在執行 {tool}...")

            # Tool completed
            elif event_type == "tool_calling_result":
                tool = (
                    data.get("name")
                    or data.get("tool_name")
                    or "unknown"
                )

                result = data.get("result") or {}

                if not isinstance(result, dict):
                    result = {}

                outcome = str(
                    result.get("status") or "unknown"
                ).lower()

                call_id = (
                    data.get("tool_call_id")
                    or data.get("id")
                )

                params = (
                    result.get("params")
                    or data.get("params")
                    or tool_params_by_id.get(call_id)
                    or {}
                )

                if not isinstance(params, dict):
                    params = {}

                message = describe_tool(
                    tool,
                    result,
                    str(data.get("description") or ""),
                    params,
                )

                matched = False

                for item in reversed(tool_history):
                    same_id = (
                        bool(call_id)
                        and item["id"] == call_id
                    )

                    same_tool = item["tool"] == tool

                    if (
                        item["status"] == "running"
                        and (same_id or same_tool)
                    ):
                        item.update(
                            status=outcome,
                            message=message,
                        )
                        matched = True
                        break

                if not matched:
                    tool_history.append(
                        {
                            "tool": tool,
                            "status": outcome,
                            "id": call_id,
                            "message": message,
                        }
                    )

                # Register successful Grafana render requests
                if (
                    self.valves.SHOW_RENDERED_IMAGES
                    and outcome == "success"
                    and tool in {
                        "grafana_render_panel",
                        "grafana_render_dashboard",
                    }
                    and len(render_jobs) < self.valves.MAX_IMAGES
                ):
                    render_jobs.append(
                        {
                            "tool": tool,
                            "params": dict(params),
                        }
                    )

                await status(message)

            # Intermediate AI message
            elif event_type == "ai_message":
                content = data.get("content")

                if isinstance(content, str) and content.strip():
                    await status("AI 正在分析調查結果...")

            # Final answer
            elif event_type == "ai_answer_end":
                final_answer = (
                    data.get("analysis")
                    or data.get("answer")
                    or ""
                )

                completed = True

                await status("調查分析完成")

            elif event_type == "approval_required":
                await status("部分操作需要額外核准")

            elif event_type == "error":
                error = (
                    data.get("message")
                    or data.get("error")
                    or "Unknown error"
                )

                await status(
                    "調查失敗：" + str(error)[:120]
                )

        # ---------------------------------------------------------
        # Process a complete SSE event
        # ---------------------------------------------------------

        async def process_sse():
            nonlocal pending_event, pending_data

            if not pending_data:
                pending_event = ""
                return

            raw_data = "\n".join(pending_data)
            event_type = pending_event or "message"

            pending_event = ""
            pending_data = []

            if raw_data == "[DONE]":
                return

            try:
                data = json.loads(raw_data)
            except json.JSONDecodeError:
                return

            if isinstance(data, dict):
                await handle_event(event_type, data)

        # ---------------------------------------------------------
        # Validate Grafana rendering parameters
        # ---------------------------------------------------------

        def render_request(job):
            params = job["params"]

            uid = str(
                params.get("dashboard_uid") or ""
            )

            if not re.fullmatch(
                r"[A-Za-z0-9_-]{1,80}", uid
            ):
                raise ValueError(
                    "缺少或不合法的 Dashboard UID"
                )

            if job["tool"] == "grafana_render_panel":
                panel_id = int(
                    params.get("panel_id", 0)
                )

                if not 1 <= panel_id <= 100000:
                    raise ValueError(
                        "缺少或不合法的 Panel ID"
                    )

                path = (
                    f"render/d-solo/"
                    f"{quote(uid, safe='')}/_"
                )

                title = f"Grafana Panel {panel_id}"

            else:
                panel_id = None

                path = (
                    f"render/d/"
                    f"{quote(uid, safe='')}/_"
                )

                title = "Grafana Dashboard"

            # Allow relative Grafana times or epoch milliseconds
            def safe_time(value, fallback):
                value = str(value or fallback)

                pattern = (
                    r"(?:now(?:-[0-9]{1,4}[smhdw])?"
                    r"|[0-9]{13})"
                )

                if re.fullmatch(pattern, value):
                    return value

                return fallback

            query = {
                "orgId": self.valves.GRAFANA_ORG_ID,
                "from": safe_time(
                    params.get("from_time"), "now-1h"
                ),
                "to": safe_time(
                    params.get("to_time"), "now"
                ),
                "width": min(
                    max(
                        int(params.get("width") or 1000),
                        400,
                    ),
                    1400,
                ),
                "height": min(
                    max(
                        int(params.get("height") or 500),
                        250,
                    ),
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

            timezone = str(
                params.get("timezone") or ""
            )

            if re.fullmatch(
                r"[A-Za-z0-9_+./-]{1,64}",
                timezone,
            ):
                query["tz"] = timezone

            variables = params.get("variables")

            if isinstance(variables, str):
                for pair in variables.split(";")[:12]:
                    if "=" not in pair:
                        continue

                    key, value = [
                        part.strip()
                        for part in pair.split("=", 1)
                    ]

                    if (
                        re.fullmatch(
                            r"var-[A-Za-z0-9_-]{1,64}",
                            key,
                        )
                        and re.fullmatch(
                            r"[A-Za-z0-9_.:/ -]{1,128}",
                            value,
                        )
                    ):
                        query[key] = value

            return title, path, query

        # ---------------------------------------------------------
        # Call HolmesGPT SSE API
        # ---------------------------------------------------------

        try:
            await status(
                "HolmesGPT 正在開始 Kubernetes 調查"
            )

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
                    self.valves.HOLMES_URL.rstrip("/")
                    + "/api/chat",
                    json=payload,
                    headers=headers,
                ) as response:

                    response.raise_for_status()

                    async for line in response.aiter_lines():
                        if not line.strip():
                            await process_sse()

                        elif line.startswith(":"):
                            continue

                        elif line.startswith("event:"):
                            pending_event = line[6:].strip()

                        elif line.startswith("data:"):
                            pending_data.append(
                                line[5:].lstrip()
                            )

                await process_sse()

            # -----------------------------------------------------
            # Build final answer
            # -----------------------------------------------------

            if not completed:
                await status("調查串流已結束")

            if not final_answer:
                return (
                    "HolmesGPT 沒有回傳完整調查結論，"
                    "請檢查 HolmesGPT Logs。"
                )

            output = str(final_answer)

            # -----------------------------------------------------
            # Tool history
            # -----------------------------------------------------

            if (
                self.valves.SHOW_TOOL_HISTORY
                and tool_history
            ):
                lines = [
                    "",
                    "---",
                    "### 工具執行紀錄",
                ]

                for index, item in enumerate(
                    tool_history[:30], 1
                ):
                    symbol = (
                        "✓"
                        if item["status"] == "success"
                        else "!"
                    )

                    lines.append(
                        f"{index}. {symbol} "
                        f"`{item['tool']}` — "
                        f"{item.get('message', item['status'])}"
                    )

                if len(tool_history) > 30:
                    lines.append(
                        f"另外還有 "
                        f"{len(tool_history) - 30} 次工具呼叫"
                    )

                output += "\n".join(lines)

            # -----------------------------------------------------
            # Grafana PNG display
            # -----------------------------------------------------

            if render_jobs and __event_emitter__:
                if not self.valves.GRAFANA_TOKEN:
                    output += (
                        "\n\n> 圖片尚未顯示："
                        "請在 Pipe Valves 設定 "
                        "GRAFANA_TOKEN。"
                    )

                else:
                    shown = 0
                    failures = []

                    grafana_headers = {
                        "Authorization": (
                            f"Bearer {self.valves.GRAFANA_TOKEN}"
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

                        for job in render_jobs:
                            try:
                                title, path, query = (
                                    render_request(job)
                                )

                                response = (
                                    await grafana_client.get(
                                        self.valves.GRAFANA_URL.rstrip("/")
                                        + "/"
                                        + path,
                                        params=query,
                                        headers=grafana_headers,
                                    )
                                )

                                response.raise_for_status()

                                png = response.content

                                # Validate actual PNG signature
                                if not png.startswith(
                                    b"\x89PNG\r\n\x1a\n"
                                ):
                                    raise ValueError(
                                        "Grafana 未回傳 PNG"
                                    )

                                if (
                                    len(png)
                                    > self.valves.MAX_IMAGE_BYTES
                                ):
                                    raise ValueError(
                                        "PNG 太大，已跳過嵌入"
                                    )

                                image64 = base64.b64encode(
                                    png
                                ).decode("ascii")

                                safe_title = html.escape(title)

                                embed_html = (
                                    '<!doctype html>'
                                    '<html>'
                                    '<head>'
                                    '<meta charset="utf-8">'
                                    '<meta name="viewport" '
                                    'content="width=device-width,'
                                    'initial-scale=1">'
                                    '</head>'
                                    '<body style="margin:0;'
                                    'padding:8px;'
                                    'background:#111;'
                                    'color:#eee">'
                                    '<div style="font:14px sans-serif;'
                                    'margin-bottom:8px">'
                                    f'{safe_title}'
                                    '</div>'
                                    f'<img alt="{safe_title}" '
                                    f'src="data:image/png;base64,'
                                    f'{image64}" '
                                    'style="display:block;'
                                    'width:100%;'
                                    'height:auto;'
                                    'max-width:100%">'
                                    '</body></html>'
                                )

                                await __event_emitter__(
                                    {
                                        "type": "embeds",
                                        "data": {
                                            "embeds": [
                                                embed_html
                                            ]
                                        },
                                    }
                                )

                                shown += 1

                            except (
                                httpx.HTTPError,
                                ValueError,
                                TypeError,
                                OverflowError,
                            ) as exc:
                                failures.append(
                                    f"{job['tool']}: "
                                    f"{type(exc).__name__}"
                                )

                    if shown:
                        output += (
                            f"\n\n已附上 {shown} 張 "
                            "Grafana 渲染圖片。"
                        )

                    if failures:
                        output += (
                            "\n\n> 部分圖片無法顯示（"
                            + "、".join(failures)
                            + "）；請檢查 Grafana 認證、"
                            "時間變數及 Pipe 連線。"
                        )

            elif render_jobs and not __event_emitter__:
                output += (
                    "\n\n> 當前請求沒有可用的 "
                    "Open WebUI 事件通道，"
                    "無法內嵌圖片。"
                )

            await status(
                "Kubernetes 調查完成",
                done=True,
            )

            return output

        # ---------------------------------------------------------
        # Error handling
        # ---------------------------------------------------------

        except httpx.TimeoutException:
            await status(
                "HolmesGPT 調查逾時",
                done=True,
            )

            return (
                "HolmesGPT 調查逾時，"
                "請查看服務 Logs。"
            )

        except httpx.HTTPStatusError as exc:
            await status(
                "HolmesGPT HTTP 請求失敗",
                done=True,
            )

            return (
                "HolmesGPT API HTTP 錯誤："
                f"{exc.response.status_code}"
            )

        except httpx.RequestError:
            await status(
                "HolmesGPT 連線失敗",
                done=True,
            )

            return (
                "無法連線 HolmesGPT，"
                "請檢查 Service、DNS 與 NetworkPolicy。"
            )

        except Exception as exc:
            await status(
                "HolmesGPT Pipe 執行失敗",
                done=True,
            )

            return (
                "Pipe 處理錯誤："
                f"{type(exc).__name__}"
            )
