
"""
title: HolmesGPT SRE
description: Kubernetes SRE Agent with Grafana PNG, auto-resize and live tool progress
version: 2.5.0
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
            description="HolmesGPT API key, if required",
            json_schema_extra={"input": {"type": "password"}},
        )

        TIMEOUT: int = Field(
            default=300,
            ge=30,
            le=900,
        )

        SHOW_TOOL_HISTORY: bool = Field(
            default=True,
        )

        GRAFANA_URL: str = Field(
            default="http://grafana.monitoring.svc.cluster.local:80",
            description="Internal Grafana service URL",
        )

        GRAFANA_TOKEN: str = Field(
            default="",
            description="Grafana Viewer Token",
            json_schema_extra={"input": {"type": "password"}},
        )

        GRAFANA_ORG_ID: int = Field(
            default=1,
        )

        SHOW_RENDERED_IMAGES: bool = Field(
            default=True,
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
        )

    def __init__(self):
        self.name = "HolmesGPT SRE"
        self.valves = self.Valves()

    # =========================================================
    # Clean internal image references
    # =========================================================

    @staticmethod
    def clean_tool_images(value):

        text = str(value or "")

        # HolmesGPT internal image reference is not
        # directly supported by Open WebUI Markdown.

        text = re.sub(
            r"!\[([^\]]*)\]\(\s*tool-image://[^)\s]+\s*\)",
            lambda m: (
                "\n\n*" + m.group(1) + "*\n\n"
                if m.group(1).strip()
                else ""
            ),
            text,
            flags=re.IGNORECASE,
        )

        text = re.sub(
            r"tool-image://[A-Za-z0-9_.:/-]+",
            "",
            text,
            flags=re.IGNORECASE,
        )

        return text.strip()

    # =========================================================
    # Grafana parameter validation
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

    def build_render_request(self, job):

        params = job.get("params") or {}
        tool = job.get("tool")

        uid = str(
            params.get("dashboard_uid") or ""
        ).strip()

        if not re.fullmatch(
            r"[A-Za-z0-9_-]{1,80}",
            uid,
        ):
            raise ValueError("Invalid Dashboard UID")

        if tool == "grafana_render_panel":

            panel_id = int(
                params.get("panel_id") or 0
            )

            if not 1 <= panel_id <= 100000:
                raise ValueError("Invalid Panel ID")

            path = (
                "render/d-solo/"
                + quote(uid, safe="")
                + "/_"
            )

            title = f"Grafana Panel {panel_id}"

        elif tool == "grafana_render_dashboard":

            panel_id = None

            path = (
                "render/d/"
                + quote(uid, safe="")
                + "/_"
            )

            title = "Grafana Dashboard"

        else:
            raise ValueError("Unsupported render tool")

        query = {
            "orgId": self.valves.GRAFANA_ORG_ID,
            "from": self.safe_time(
                params.get("from_time"),
                "now-1h",
            ),
            "to": self.safe_time(
                params.get("to_time"),
                "now",
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

        timezone = str(
            params.get("timezone") or ""
        )

        if re.fullmatch(
            r"[A-Za-z0-9_+./-]{1,64}",
            timezone,
        ):
            query["tz"] = timezone

        # Grafana template variables
        #
        # variables:
        # var-cluster=k8skf;
        # var-node=UTCKFK8SINF02;
        # var-instance=10.26.166.232:9100

        variables = params.get("variables")

        if isinstance(variables, str):

            for pair in variables.split(";")[:12]:

                if "=" not in pair:
                    continue

                key, value = [
                    x.strip()
                    for x in pair.split("=", 1)
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
    # Auto-resizing Grafana image embed
    # =========================================================

    @staticmethod
    def make_image_embed(title, image_b64):

        safe_title = html.escape(title, quote=True)

        # Open WebUI Rich UI iframe:
        # Report the actual HTML content height to the parent.
        # This avoids a fixed-height iframe scrollbar.

        return (
            '<!doctype html>'
            '<html>'
            '<head>'
            '<meta charset="utf-8">'
            '<meta name="viewport" '
            'content="width=device-width,initial-scale=1">'
            '<style>'
            '*{box-sizing:border-box;}'
            'html,body{'
            'margin:0;'
            'padding:0;'
            'width:100%;'
            'overflow:hidden;'
            'background:transparent;'
            'font-family:system-ui,sans-serif;'
            '}'
            '.grafana-wrap{'
            'width:100%;'
            'margin:0;'
            'padding:0;'
            '}'
            '.grafana-image{'
            'display:block;'
            'width:100%;'
            'max-width:100%;'
            'height:auto;'
            'object-fit:contain;'
            'border-radius:8px;'
            '}'
            '</style>'
            '</head>'
            '<body>'
            '<div class="grafana-wrap">'
            f'<img id="grafana-image" class="grafana-image" '
            f'alt="{safe_title}" '
            f'src="data:image/png;base64,{image_b64}">'
            '</div>'
            '<script>'
            '(function(){'
            'function reportHeight(){'
            'var h=Math.ceil('
            'document.documentElement.scrollHeight'
            ');'
            'parent.postMessage('
            '{type:"iframe:height",height:h},"*");'
            '}'
            'window.addEventListener("load",reportHeight);'
            'window.addEventListener("resize",reportHeight);'
            'var img=document.getElementById("grafana-image");'
            'if(img){'
            'img.addEventListener("load",reportHeight);'
            '}'
            'if(typeof ResizeObserver!=="undefined"){'
            'new ResizeObserver(reportHeight).observe('
            'document.body'
            ');'
            '}'
            'reportHeight();'
            '})();'
            '</script>'
            '</body>'
            '</html>'
        )

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

        # Admin-only while using the shared
        # HolmesGPT ServiceAccount and Grafana Viewer token.

        if not __user__ or __user__.get("role") != "admin":
            return "Access denied: administrator access required."

        # Open WebUI title/tag/metadata tasks

        if __task__:
            return "HolmesGPT SRE"

        messages = body.get("messages", [])

        questions = [
            m.get("content")
            for m in messages
            if m.get("role") == "user"
            and isinstance(m.get("content"), str)
        ]

        if not questions or not questions[-1].strip():
            return "請輸入 Kubernetes 維運問題。"

        question = questions[-1].strip()

        system_prompt = "\n\n".join(
            m.get("content", "")
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

        render_jobs = []
        render_keys = set()

        final_answer = None
        pending_event = ""
        pending_data = []
        completed = False

        # -----------------------------------------------------
        # Open WebUI status
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
        # Tool event handling
        # -----------------------------------------------------

        async def handle_event(event_type, data):

            nonlocal final_answer, completed

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

                tool_history.append(
                    {
                        "tool": tool,
                        "status": "running",
                        "id": call_id,
                        "message": "執行中",
                    }
                )

                await status(f"正在執行 {tool}...")

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

                if outcome == "success":

                    if tool in {
                        "grafana_render_panel",
                        "grafana_render_dashboard",
                    }:
                        message = "Grafana 圖片渲染成功"

                    elif tool == "fetch_skill":
                        message = "已載入調查 Skill"

                    elif tool.startswith("kubernetes"):
                        message = "Kubernetes 資源查詢完成"

                    elif (
                        tool.startswith("grafana")
                        or "prometheus" in tool
                        or tool in {
                            "list_datasources",
                            "get_metric_names",
                            "get_label_values",
                        }
                    ):
                        message = "監控指標查詢完成"

                    else:
                        message = f"{tool} 執行完成"

                else:

                    error = str(
                        result.get("error")
                        or "工具執行未成功"
                    )[:140]

                    message = f"{tool} 失敗：{error}"

                matched = False

                for item in reversed(tool_history):

                    same_id = (
                        bool(call_id)
                        and item["id"] == call_id
                    )

                    same_tool = (
                        item["tool"] == tool
                    )

                    if item["status"] == "running" and (
                        same_id or same_tool
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

                # Only render images from successful
                # known Grafana tools.

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

            elif event_type == "ai_message":

                if data.get("content"):
                    await status("AI 正在分析調查結果...")

            elif event_type == "ai_answer_end":

                final_answer = (
                    data.get("analysis")
                    or data.get("answer")
                    or ""
                )

                completed = True

                await status("分析完成")

            elif event_type == "approval_required":

                await status("部分操作需要額外核准")

            elif event_type == "error":

                error = (
                    data.get("message")
                    or data.get("error")
                    or "Unknown error"
                )

                await status(
                    "調查失敗：" + str(error)[:140]
                )

        # -----------------------------------------------------
        # Parse SSE
        # -----------------------------------------------------

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

        # =====================================================
        # Call HolmesGPT
        # =====================================================

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
                    json=payload,
                    headers=headers,
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

            if not completed:
                await status("調查串流已結束")

            if not final_answer:

                await status(
                    "HolmesGPT 沒有回傳完整回答",
                    done=True,
                )

                return (
                    "HolmesGPT 沒有回傳完整調查結論。"
                    "請檢查 HolmesGPT Logs。"
                )

            # -------------------------------------------------
            # Remove invalid tool-image:// markdown
            # -------------------------------------------------

            output = str(final_answer)

            if self.valves.REMOVE_TOOL_IMAGE_REFERENCES:
                output = self.clean_tool_images(output)

            # -------------------------------------------------
            # Tool history
            # -------------------------------------------------

            if self.valves.SHOW_TOOL_HISTORY and tool_history:

                lines = [
                    "",
                    "---",
                    "### 工具執行紀錄",
                ]

                for i, item in enumerate(tool_history[:30], 1):

                    mark = (
                        "✓"
                        if item["status"] == "success"
                        else "!"
                    )

                    lines.append(
                        f"{i}. {mark} "
                        f"`{item['tool']}` — "
                        f"{item.get('message', item['status'])}"
                    )

                if len(tool_history) > 30:
                    lines.append(
                        f"另有 {len(tool_history) - 30} 次工具呼叫"
                    )

                output += "\n".join(lines)

            # =================================================
            # Render Grafana images
            # =================================================

            if render_jobs and __event_emitter__:

                if not self.valves.GRAFANA_TOKEN:

                    output += (
                        "\n\n> 未設定 GRAFANA_TOKEN，"
                        "因此沒有顯示 Grafana 圖片。"
                    )

                else:

                    shown = 0
                    failures = []

                    grafana_headers = {
                        "Authorization": (
                            "Bearer "
                            + self.valves.GRAFANA_TOKEN
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
                                    self.build_render_request(job)
                                )

                                url = (
                                    self.valves.GRAFANA_URL.rstrip("/")
                                    + "/"
                                    + path
                                )

                                response = (
                                    await grafana_client.get(
                                        url,
                                        params=query,
                                        headers=grafana_headers,
                                    )
                                )

                                response.raise_for_status()

                                content_type = (
                                    response.headers.get(
                                        "Content-Type", ""
                                    )
                                    .split(";")[0]
                                    .strip()
                                    .lower()
                                )

                                if content_type != "image/png":
                                    raise ValueError(
                                        "Grafana did not return image/png"
                                    )

                                png = response.content

                                if not png.startswith(
                                    b"\x89PNG\r\n\x1a\n"
                                ):
                                    raise ValueError(
                                        "Invalid PNG signature"
                                    )

                                if len(png) > self.valves.MAX_IMAGE_BYTES:
                                    raise ValueError(
                                        "PNG exceeds size limit"
                                    )

                                image_b64 = (
                                    base64.b64encode(png)
                                    .decode("ascii")
                                )

                                embed_html = self.make_image_embed(
                                    title,
                                    image_b64,
                                )

                                await __event_emitter__(
                                    {
                                        "type": "embeds",
                                        "data": {
                                            "embeds": [embed_html],
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
                            "Grafana 圖片。"
                        )

                    if failures:
                        output += (
                            "\n\n> 部分圖片顯示失敗："
                            + "、".join(failures)
                        )

            # -------------------------------------------------
            # Complete
            # -------------------------------------------------

            await status(
                "Kubernetes 調查完成",
                done=True,
            )

            return output

        except httpx.TimeoutException:

            await status(
                "HolmesGPT 調查逾時",
                done=True,
            )

            return "HolmesGPT 調查逾時，請檢查 Logs。"

        except httpx.HTTPStatusError as exc:

            await status(
                "HolmesGPT HTTP 請求失敗",
                done=True,
            )

            return (
                "HolmesGPT API HTTP 錯誤："
                + str(exc.response.status_code)
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
                + type(exc).__name__
            )
