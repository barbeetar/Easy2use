
"""
title: HolmesGPT SRE
description: HolmesGPT Kubernetes SRE with SSE, Grafana images and safe image references
version: 2.4.0
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
            description="HolmesGPT API key",
            json_schema_extra={"input": {"type": "password"}},
        )

        TIMEOUT: int = Field(
            default=300,
            description="Investigation timeout",
        )

        SHOW_TOOL_HISTORY: bool = Field(
            default=True,
            description="Include tool history in final answer",
        )

        GRAFANA_URL: str = Field(
            default="http://grafana.monitoring.svc.cluster.local:80",
            description="Grafana internal URL",
        )

        GRAFANA_TOKEN: str = Field(
            default="",
            description="Grafana Viewer Service Account Token",
            json_schema_extra={"input": {"type": "password"}},
        )

        GRAFANA_ORG_ID: int = Field(
            default=1,
            description="Grafana organization ID",
        )

        SHOW_RENDERED_IMAGES: bool = Field(
            default=True,
            description="Show Grafana PNG images",
        )

        MAX_IMAGES: int = Field(
            default=2,
            ge=1,
            le=4,
        )

        MAX_IMAGE_BYTES: int = Field(
            default=1500000,
            ge=100000,
            le=3000000,
        )

        REMOVE_TOOL_IMAGE_REFERENCES: bool = Field(
            default=True,
            description="Remove unsupported tool-image references",
        )

    def __init__(self):

        self.name = "HolmesGPT SRE"

        self.valves = self.Valves()

    # =========================================================
    # Remove unsupported HolmesGPT image references
    # =========================================================

    @staticmethod
    def clean_tool_images(text):

        if not isinstance(text, str):
            return ""

        # Example:
        # ![CPU Usage](tool-image://chatcmpl-tool-xxx)
        #
        # Open WebUI cannot resolve this private URI.
        # Preserve alt text as a caption instead.

        text = re.sub(
            r'!\[([^\]]*)\]\(\s*tool-image://[^)\s]+\s*\)',
            lambda m: (
                f"\n\n*{m.group(1)}*\n\n"
                if m.group(1).strip()
                else ""
            ),
            text,
            flags=re.IGNORECASE,
        )

        # Remove remaining orphan references.

        text = re.sub(
            r'tool-image://[A-Za-z0-9_.:/-]+',
            "",
            text,
            flags=re.IGNORECASE,
        )

        return text.strip()

    # =========================================================
    # Process Grafana render parameters
    # =========================================================

    def build_render_request(self, job):

        params = job.get("params") or {}

        uid = str(
            params.get("dashboard_uid") or ""
        ).strip()

        if not re.fullmatch(
            r"[A-Za-z0-9_-]{1,80}",
            uid,
        ):
            raise ValueError("Invalid Dashboard UID")

        tool = job["tool"]

        if tool == "grafana_render_panel":

            panel_id = int(
                params.get("panel_id") or 0
            )

            if not 1 <= panel_id <= 100000:
                raise ValueError("Invalid Panel ID")

            path = (
                f"render/d-solo/"
                f"{quote(uid, safe='')}/_"
            )

            title = f"Grafana Panel {panel_id}"

        elif tool == "grafana_render_dashboard":

            panel_id = None

            path = (
                f"render/d/"
                f"{quote(uid, safe='')}/_"
            )

            title = "Grafana Dashboard"

        else:

            raise ValueError("Unsupported rendering tool")

        # -----------------------------------------------------
        # Validate time range
        # -----------------------------------------------------

        def safe_time(value, fallback):

            value = str(value or fallback)

            pattern = (
                r"(?:now(?:-[0-9]{1,4}[smhdw])?"
                r"|[0-9]{13})"
            )

            if re.fullmatch(pattern, value):
                return value

            return fallback

        # -----------------------------------------------------
        # Grafana render parameters
        # -----------------------------------------------------

        query = {
            "orgId": self.valves.GRAFANA_ORG_ID,
            "from": safe_time(
                params.get("from_time"),
                "now-1h",
            ),
            "to": safe_time(
                params.get("to_time"),
                "now",
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

        # -----------------------------------------------------
        # Timezone
        # -----------------------------------------------------

        timezone = str(
            params.get("timezone") or ""
        )

        if re.fullmatch(
            r"[A-Za-z0-9_+./-]{1,64}",
            timezone,
        ):
            query["tz"] = timezone

        # -----------------------------------------------------
        # Grafana dashboard variables
        #
        # Example:
        # var-cluster=k8skf;
        # var-node=UTCKFK8SINF02;
        # var-instance=10.26.166.232:9100
        # -----------------------------------------------------

        variables = params.get("variables")

        if isinstance(variables, str):

            for pair in variables.split(";")[:12]:

                if "=" not in pair:
                    continue

                key, value = [
                    s.strip()
                    for s in pair.split("=", 1)
                ]

                if not re.fullmatch(
                    r"var-[A-Za-z0-9_-]{1,64}",
                    key,
                ):
                    continue

                if not re.fullmatch(
                    r"[A-Za-z0-9_.:/ -]{1,128}",
                    value,
                ):
                    continue

                query[key] = value

        return title, path, query

    # =========================================================
    # Main Pipe
    # =========================================================

    async def pipe(
        self,
        body: dict,
        __user__: dict = None,
        __event_emitter__=None,
        __task__=None,
    ):

        # -----------------------------------------------------
        # Administrator-only access
        # -----------------------------------------------------

        if (
            not __user__
            or __user__.get("role") != "admin"
        ):

            return (
                "Access denied: "
                "administrator access required."
            )

        # -----------------------------------------------------
        # Open WebUI background metadata tasks
        # -----------------------------------------------------

        if __task__:

            return "HolmesGPT SRE"

        # -----------------------------------------------------
        # Read user question
        # -----------------------------------------------------

        messages = body.get("messages", [])

        questions = [
            m.get("content")
            for m in messages
            if (
                m.get("role") == "user"
                and isinstance(m.get("content"), str)
            )
        ]

        if not questions:

            return "請輸入 Kubernetes 維運問題。"

        question = questions[-1].strip()

        if not question:

            return "請輸入 Kubernetes 維運問題。"

        # -----------------------------------------------------
        # Forward Open WebUI system prompt
        # -----------------------------------------------------

        system_prompt = "\n\n".join(
            str(m.get("content", ""))
            for m in messages
            if (
                m.get("role") == "system"
                and isinstance(m.get("content"), str)
            )
        )

        # -----------------------------------------------------
        # HolmesGPT HTTP request
        # -----------------------------------------------------

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

        # -----------------------------------------------------
        # Investigation state
        # -----------------------------------------------------

        tool_history = []

        tool_params_by_id = {}

        render_jobs = []

        render_keys = set()

        final_answer = None

        pending_event = ""

        pending_data = []

        completed = False

        # -----------------------------------------------------
        # Open WebUI status events
        # -----------------------------------------------------

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

        # -----------------------------------------------------
        # Describe tool result
        # -----------------------------------------------------

        def describe_tool(tool, outcome, result):

            if outcome != "success":

                error = str(
                    result.get("error")
                    or "工具執行未成功"
                )

                return (
                    f"{tool} 失敗："
                    f"{error[:140]}"
                )

            if tool in {
                "grafana_render_panel",
                "grafana_render_dashboard",
            }:

                return "Grafana 圖片渲染成功"

            if tool == "fetch_skill":

                return "已取得調查 Skill"

            if (
                tool.startswith("grafana")
                or tool.startswith("query_prometheus")
                or tool.startswith("execute_prometheus")
                or tool in {
                    "get_metric_names",
                    "get_label_values",
                    "list_datasources",
                }
            ):

                return "監控工具查詢完成"

            if tool.startswith("kubernetes"):

                return "Kubernetes 資源查詢完成"

            return f"{tool} 執行完成"

        # -----------------------------------------------------
        # HolmesGPT SSE event handler
        # -----------------------------------------------------

        async def handle_event(event_type, data):

            nonlocal final_answer, completed

            # -------------------------------------------------
            # Tool started
            # -------------------------------------------------

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

                if (
                    call_id
                    and isinstance(start_params, dict)
                ):

                    tool_params_by_id[call_id] = (
                        start_params
                    )

                tool_history.append(
                    {
                        "tool": tool,
                        "status": "running",
                        "id": call_id,
                        "message": "執行中",
                    }
                )

                await status(
                    f"正在執行 {tool}..."
                )

            # -------------------------------------------------
            # Tool completed
            # -------------------------------------------------

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
                    outcome,
                    result,
                )

                # ---------------------------------------------
                # Match previous tool start event
                # ---------------------------------------------

                matched = False

                for item in reversed(tool_history):

                    same_id = (
                        bool(call_id)
                        and item["id"] == call_id
                    )

                    same_tool = (
                        item["tool"] == tool
                    )

                    if (
                        item["status"] == "running"
                        and (
                            same_id
                            or same_tool
                        )
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

                # ---------------------------------------------
                # Collect successful Grafana render jobs
                #
                # We do not store large base64 images here.
                # The Pipe will fetch PNG from Grafana.
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
                        key not in render_keys
                        and len(render_jobs)
                        < self.valves.MAX_IMAGES
                    ):

                        render_keys.add(key)

                        render_jobs.append(
                            {
                                "tool": tool,
                                "params": dict(params),
                            }
                        )

                await status(message)

            # -------------------------------------------------
            # Intermediate model message
            # -------------------------------------------------

            elif event_type == "ai_message":

                content = data.get("content")

                if (
                    isinstance(content, str)
                    and content.strip()
                ):

                    await status(
                        "AI 正在分析調查結果..."
                    )

            # -------------------------------------------------
            # Final AI answer
            # -------------------------------------------------

            elif event_type == "ai_answer_end":

                final_answer = (
                    data.get("analysis")
                    or data.get("answer")
                    or ""
                )

                completed = True

                await status(
                    "調查分析完成"
                )

            # -------------------------------------------------
            # Approval required
            # -------------------------------------------------

            elif event_type == "approval_required":

                await status(
                    "部分操作需要額外核准"
                )

            # -------------------------------------------------
            # HolmesGPT error
            # -------------------------------------------------

            elif event_type == "error":

                error = (
                    data.get("message")
                    or data.get("error")
                    or "Unknown error"
                )

                await status(
                    "調查失敗："
                    + str(error)[:140]
                )

        # -----------------------------------------------------
        # Parse one complete SSE event
        # -----------------------------------------------------

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

                await handle_event(
                    event_type,
                    data,
                )

        # =====================================================
        # Execute investigation
        # =====================================================

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
                    holmes_url,
                    json=payload,
                    headers=headers,
                ) as response:

                    response.raise_for_status()

                    async for line in response.aiter_lines():

                        # Empty line means SSE event completed.

                        if not line.strip():

                            await process_sse()
                            continue

                        # Ignore keepalive comments.

                        if line.startswith(":"):
                            continue

                        if line.startswith("event:"):

                            pending_event = (
                                line[6:].strip()
                            )

                        elif line.startswith("data:"):

                            pending_data.append(
                                line[5:].lstrip()
                            )

                    # Flush final SSE event.

                    await process_sse()

            # -------------------------------------------------
            # Final result validation
            # -------------------------------------------------

            if not completed:

                await status(
                    "調查串流已結束"
                )

            if not final_answer:

                await status(
                    "HolmesGPT 沒有回傳完整回答",
                    done=True,
                )

                return (
                    "HolmesGPT 沒有回傳完整調查結論。"
                    "請檢查 HolmesGPT Logs。"
                )

            # =================================================
            # Fix unsupported tool-image:// links
            # =================================================

            output = str(final_answer)

            if self.valves.REMOVE_TOOL_IMAGE_REFERENCES:

                output = self.clean_tool_images(
                    output
                )

            # -------------------------------------------------
            # Tool execution history
            # -------------------------------------------------

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
                    tool_history[:30],
                    1,
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

            # =================================================
            # Display Grafana images
            # =================================================

            if render_jobs and __event_emitter__:

                if not self.valves.GRAFANA_TOKEN:

                    output += (
                        "\n\n> Grafana 圖片未顯示："
                        "請先設定 GRAFANA_TOKEN。"
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
                                    self.build_render_request(
                                        job
                                    )
                                )

                                # ---------------------------------
                                # Fetch PNG from internal Grafana.
                                # No arbitrary model-provided URL.
                                # ---------------------------------

                                render_url = (
                                    self.valves.GRAFANA_URL.rstrip("/")
                                    + "/"
                                    + path
                                )

                                response = (
                                    await grafana_client.get(
                                        render_url,
                                        params=query,
                                        headers=grafana_headers,
                                    )
                                )

                                response.raise_for_status()

                                if (
                                    response.headers.get(
                                        "Content-Type", ""
                                    ).split(";")[0].strip().lower()
                                    != "image/png"
                                ):

                                    raise ValueError(
                                        "Grafana did not return image/png"
                                    )

                                png = response.content

                                # ---------------------------------
                                # Validate PNG signature.
                                # ---------------------------------

                                if not png.startswith(
                                    b"\x89PNG\r\n\x1a\n"
                                ):

                                    raise ValueError(
                                        "Invalid PNG data"
                                    )

                                if (
                                    len(png)
                                    > self.valves.MAX_IMAGE_BYTES
                                ):

                                    raise ValueError(
                                        "PNG exceeds image size limit"
                                    )

                                image64 = (
                                    base64.b64encode(
                                        png
                                    ).decode("ascii")
                                )

                                safe_title = (
                                    html.escape(title)
                                )

                                # ---------------------------------
                                # Open WebUI embed.
                                # This is independent from the
                                # HolmesGPT text response.
                                # ---------------------------------

                                embed_html = (
                                    '<!doctype html>'
                                    '<html>'
                                    '<head>'
                                    '<meta charset="utf-8">'
                                    '<meta name="viewport" '
                                    'content="width=device-width,'
                                    'initial-scale=1">'
                                    '</head>'
                                    '<body style="'
                                    'margin:0;'
                                    'padding:8px;'
                                    'background:#111;'
                                    'color:#eee;'
                                    'font-family:sans-serif">'
                                    '<div style="'
                                    'font-size:14px;'
                                    'margin-bottom:8px">'
                                    f'{safe_title}'
                                    '</div>'
                                    '<img '
                                    f'alt="{safe_title}" '
                                    f'src="data:image/png;base64,{image64}" '
                                    'style="'
                                    'display:block;'
                                    'width:100%;'
                                    'height:auto;'
                                    'max-width:100%">'
                                    '</body>'
                                    '</html>'
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
                            "\n\n> 部分圖片無法顯示："
                            + "、".join(failures)
                        )

            elif render_jobs and not __event_emitter__:

                output += (
                    "\n\n> 沒有可用的圖片事件通道，"
                    "無法顯示 Grafana PNG。"
                )

            # -------------------------------------------------
            # Complete
            # -------------------------------------------------

            await status(
                "Kubernetes 調查完成",
                done=True,
            )

            return output

        # =====================================================
        # Error handling
        # =====================================================

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
