
"""
title: HolmesGPT SRE Interactive
version: 5.2.0
description: HolmesGPT SRE with Prometheus interactive charts, Grafana MCP image embedding and timing
"""

import base64
import binascii
import hashlib
import html
import json
import math
import re
import time
from datetime import datetime
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field


class Pipe:
    class Valves(BaseModel):
        HOLMES_URL: str = Field(
            default="http://holmesgpt-holmes.holmesgpt.svc.cluster.local:80"
        )
        MODEL: str = Field(default="qwen-local")
        API_KEY: str = Field(
            default="",
            json_schema_extra={"input": {"type": "password"}},
        )
        TIMEOUT: int = Field(default=600, ge=30, le=1800)
        SHOW_TOOL_HISTORY: bool = Field(default=True)
        SHOW_TIMINGS: bool = Field(default=True)
        SHOW_PROMETHEUS_CHARTS: bool = Field(default=True)
        SHOW_RENDERED_IMAGES: bool = Field(default=True)
        REMOVE_TOOL_IMAGE_REFERENCES: bool = Field(default=True)
        MAX_CHARTS: int = Field(default=3, ge=1, le=5)
        MAX_SERIES: int = Field(default=12, ge=1, le=20)
        MAX_POINTS_PER_SERIES: int = Field(
            default=240, ge=20, le=500
        )
        GRAFANA_URL: str = Field(
            default="http://grafana.monitoring.svc.cluster.local:80"
        )
        GRAFANA_TOKEN: str = Field(
            default="",
            json_schema_extra={"input": {"type": "password"}},
        )
        GRAFANA_ORG_ID: int = Field(default=1)
        MAX_IMAGES: int = Field(default=2, ge=1, le=4)
        MAX_IMAGE_BYTES: int = Field(
            default=4000000, ge=100000, le=10000000
        )

    def __init__(self):
        self.name = "HolmesGPT SRE Interactive V5.2"
        self.valves = self.Valves()

    # ============================================================
    # General Utilities
    # ============================================================

    @staticmethod
    def as_dict(value):
        if isinstance(value, dict):
            return value

        if isinstance(value, str):
            try:
                obj = json.loads(value)
                return obj if isinstance(obj, dict) else {}
            except (TypeError, ValueError):
                pass

        return {}

    @staticmethod
    def remove_tool_images(text):
        text = str(text or "")

        text = re.sub(
            r"!\[([^\]]*)\]\(\s*tool-image://[^)]+\)",
            "",
            text,
            flags=re.I,
        )

        text = re.sub(
            r"tool-image://[\w.\-:/]+",
            "",
            text,
            flags=re.I,
        )

        return text.strip()

    @staticmethod
    def parse_time(value):
        if isinstance(value, (int, float)):
            t = float(value)
            return t / 1000 if t > 1e11 else t

        if not value:
            return None

        try:
            return datetime.fromisoformat(
                str(value).replace("Z", "+00:00")
            ).timestamp()

        except (TypeError, ValueError, OverflowError):
            try:
                return Pipe.parse_time(float(value))
            except (TypeError, ValueError):
                return None

    # ============================================================
    # Prometheus Matrix
    # ============================================================

    @staticmethod
    def matrix_result(result):
        data = result.get("data")

        for _ in range(5):
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except (TypeError, ValueError):
                    return None

            if (
                not isinstance(data, dict)
                or data.get("status") == "error"
                or data.get("error_message")
            ):
                return None

            if (
                data.get("resultType") == "matrix"
                and isinstance(data.get("result"), list)
            ):
                return data

            data = data.get("data")

        return None

    @staticmethod
    def series_name(metric, index):
        for key in (
            "pod",
            "container",
            "namespace",
            "instance",
            "job",
            "__name__",
        ):
            if metric.get(key):
                return str(metric[key])

        if metric:
            return ", ".join(
                f"{k}={v}"
                for k, v in sorted(metric.items())
            )[:120]

        return f"Series {index + 1}"

    @staticmethod
    def units(params, maximum, question):
        query = str(params.get("query") or "").lower()
        output = str(params.get("output_type") or "").lower()
        description = str(
            params.get("description") or ""
        ).lower()

        q = question.lower()

        is_memory = (
            "memory" in query
            or "bytes" in query
            or "memory" in output
            or "byte" in output
        )

        # Avoid double division when PromQL already
        # converted bytes to MiB or GiB.
        is_gib = bool(
            re.search(
                r"/\s*(?:1073741824|1024\s*\^\s*3|1024\s*\*\s*1024\s*\*\s*1024)\b",
                query,
            )
        )

        is_mib = bool(
            re.search(
                r"/\s*(?:1048576|1024\s*\^\s*2|1024\s*\*\s*1024)\b",
                query,
            )
        )

        if is_memory:
            if is_gib:
                return "GiB", 1.0

            if is_mib:
                if "gib" in q:
                    return "GiB", 1024.0
                return "MiB", 1.0

            if (
                "gib" in q
                or "gb" in q
                or maximum >= 1024**3
            ):
                return "GiB", float(1024**3)

            return "MiB", float(1024**2)

        if (
            "cpu" in query
            or "cpu" in output
            or "cpu" in description
        ):
            return "CPU cores", 1.0

        if (
            "percent" in output
            or "percentage" in output
        ):
            return "%", 1.0

        return "Value", 1.0

    def chart_payload(self, job, question):
        matrix = job["matrix"]
        params = job["params"]

        start = self.parse_time(params.get("start"))
        end = self.parse_time(params.get("end"))

        if (
            start is not None
            and end is not None
            and end <= start
        ):
            return None

        parsed = []
        raw_max = 0.0

        for index, item in enumerate(
            matrix.get("result", [])[
                : self.valves.MAX_SERIES
            ]
        ):
            if not isinstance(item, dict):
                continue

            samples = {}

            for pair in item.get("values", []):
                if (
                    not isinstance(pair, (list, tuple))
                    or len(pair) < 2
                ):
                    continue

                try:
                    t = float(pair[0])
                    v = float(pair[1])

                except (
                    ValueError,
                    TypeError,
                    OverflowError,
                ):
                    continue

                if not (
                    math.isfinite(t)
                    and math.isfinite(v)
                ):
                    continue

                if (
                    start is not None
                    and t < start - 1
                ):
                    continue

                if (
                    end is not None
                    and t > end + 1
                ):
                    continue

                samples[t] = v

            points = [
                [t, v]
                for t, v in sorted(samples.items())
            ]

            if not points:
                continue

            raw_max = max(
                raw_max,
                max(abs(v) for _, v in points),
            )

            parsed.append({
                "name": self.series_name(
                    item.get("metric") or {},
                    index,
                ),
                "points": points,
            })

        if not parsed:
            return None

        unit, divisor = self.units(
            params,
            raw_max,
            question,
        )

        series = []

        for item in parsed:
            original = item["points"]
            max_points = self.valves.MAX_POINTS_PER_SERIES

            if len(original) > max_points:
                indices = sorted(
                    set(
                        round(
                            i * (len(original) - 1)
                            / (max_points - 1)
                        )
                        for i in range(max_points)
                    )
                )

                shown = [
                    original[i]
                    for i in indices
                ]

            else:
                shown = original

            values = [
                v / divisor
                for _, v in original
            ]

            step = min(
                (
                    original[i][0]
                    - original[i - 1][0]
                    for i in range(1, len(original))
                ),
                default=None,
            )

            series.append({
                "name": item["name"],
                "points": [
                    [t, v / divisor]
                    for t, v in shown
                ],
                "count": len(original),
                "displayCount": len(shown),
                "step": step,
                "current": values[-1],
                "average": sum(values) / len(values),
                "minimum": min(values),
                "maximum": max(values),
                "latestTimestamp": original[-1][0],
            })

        q = question.lower()
        style = "line"

        if (
            "horizontal" in q
            or "橫條" in q
            or ("bar" in q and len(series) > 1)
        ):
            style = "horizontal"

        elif "area" in q or "面積" in q:
            style = "area"

        elif "points" in q or "散點" in q:
            style = "points"

        elif "bars" in q or "柱狀" in q:
            style = "bars"

        return {
            "title": str(
                params.get("description")
                or "Prometheus 趨勢"
            )[:200],
            "query": str(
                params.get("query") or ""
            )[:4000],
            "unit": unit,
            "defaultStyle": style,
            "queryStart": start,
            "queryEnd": end,
            "actualStart": min(
                s["points"][0][0]
                for s in parsed
            ),
            "actualEnd": max(
                s["points"][-1][0]
                for s in parsed
            ),
            "series": series,
        }

    # ============================================================
    # Interactive Prometheus Chart
    # ============================================================

    @staticmethod
    def chart_html(payload):
        json_data = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )

        json_data = (
            json_data
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("&", "\\u0026")
        )

        template = r'''
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">

<style>
* { box-sizing:border-box; }

html,body {
  margin:0;
  overflow:hidden;
  background:transparent;
  font:13px system-ui;
  color:#e2e8f0;
}

.wrap {
  background:#101827;
  border:1px solid #334155;
  border-radius:12px;
  padding:15px;
}

.title {
  font-size:16px;
  font-weight:650;
  overflow-wrap:anywhere;
}

.sub {
  font-size:12px;
  color:#aab6c8;
  margin:5px 0 10px;
  overflow-wrap:anywhere;
}

.controls {
  display:flex;
  flex-wrap:wrap;
  gap:6px;
  margin-bottom:10px;
}

.controls button {
  color:#e2e8f0;
  background:#24344b;
  border:1px solid #475569;
  border-radius:6px;
  padding:7px;
  cursor:pointer;
}

.controls button.active {
  background:#155e75;
  border-color:#22d3ee;
}

.plot {
  height:330px;
  position:relative;
  width:100%;
}

canvas {
  width:100%;
  height:100%;
  display:block;
}

.tip {
  position:absolute;
  display:none;
  pointer-events:none;
  z-index:10;
  background:#020617ee;
  border:1px solid #64748b;
  border-radius:7px;
  padding:9px;
  max-width:320px;
  font-size:12px;
  box-shadow:0 8px 18px #0006;
}

.legend {
  display:flex;
  flex-wrap:wrap;
  gap:10px;
  margin-top:10px;
}

.legend span {
  overflow-wrap:anywhere;
}

.dot {
  display:inline-block;
  width:9px;
  height:9px;
  border-radius:2px;
  margin-right:5px;
}

.stats {
  display:flex;
  flex-wrap:wrap;
  gap:8px;
  margin:10px 0;
}

.stat {
  background:#1e293b;
  border-radius:7px;
  padding:8px;
}

.stat b {
  display:block;
  margin-top:3px;
}

.notice {
  color:#fbbf24;
  font-size:12px;
}

details {
  margin-top:10px;
  color:#cbd5e1;
}

pre {
  white-space:pre-wrap;
  overflow-wrap:anywhere;
}
</style>
</head>

<body>

<div class="wrap">

  <div class="title" id="title"></div>
  <div class="sub" id="sub"></div>

  <div class="controls" id="controls">
    <button data-type="line">Line 折線</button>
    <button data-type="area">Area 面積</button>
    <button data-type="bars">Bars 柱狀</button>
    <button data-type="points">Points 散點</button>
    <button data-type="horizontal">Bar 比較</button>
    <button id="zoom" data-type="zoom">
      Y 軸：從零
    </button>
  </div>

  <div class="plot" id="plot">
    <canvas id="canvas"></canvas>
    <div class="tip" id="tip"></div>
  </div>

  <div class="legend" id="legend"></div>
  <div class="stats" id="stats"></div>
  <div class="notice" id="notice"></div>

  <details>
    <summary>查看 PromQL</summary>
    <pre id="promql"></pre>
  </details>

</div>

<script>
(function() {
"use strict";

const DATA = __PAYLOAD__;

const colors = [
 "#22d3ee","#34d399","#fbbf24","#c084fc",
 "#fb7185","#60a5fa","#f97316","#a3e635",
 "#f472b6","#2dd4bf","#eab308","#818cf8"
];

const cvs = document.getElementById("canvas");
const ctx = cvs.getContext("2d");
const plot = document.getElementById("plot");
const tip = document.getElementById("tip");
const controls = document.getElementById("controls");

let style = DATA.defaultStyle || "line";
let zero = true;
let geometry = null;
let hover = null;

const dt = new Intl.DateTimeFormat("zh-TW", {
  timeZone:"Asia/Taipei",
  year:"numeric",
  month:"2-digit",
  day:"2-digit",
  hour:"2-digit",
  minute:"2-digit",
  second:"2-digit",
  hour12:false,
  hourCycle:"h23"
});

const short = new Intl.DateTimeFormat("zh-TW", {
  timeZone:"Asia/Taipei",
  hour:"2-digit",
  minute:"2-digit",
  hour12:false,
  hourCycle:"h23"
});

const full = t => dt.format(new Date(t * 1000));
const label = t => short.format(new Date(t * 1000));

function fmt(v) {
  if (!Number.isFinite(v)) return "N/A";

  return Math.abs(v) >= 1000
    ? v.toLocaleString("en-US", {
        maximumFractionDigits:2
      })
    : Math.abs(v) >= 1
      ? v.toFixed(3)
      : v.toFixed(5);
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&":"&amp;",
    "<":"&lt;",
    ">":"&gt;",
    '"':"&quot;",
    "'":"&#39;"
  }[c]));
}

function height() {
  parent.postMessage({
    type:"iframe:height",
    height:Math.ceil(
      document.documentElement.scrollHeight
    )
  }, "*");
}

document.getElementById("title").textContent =
  DATA.title;

document.getElementById("promql").textContent =
  DATA.query;

document.getElementById("sub").textContent =
  "單位：" + DATA.unit +
  " · 台灣時間 UTC+8 · " +
  full(DATA.actualStart) + " ～ " +
  full(DATA.actualEnd) + " · " +
  DATA.series.map(s =>
    s.name + ": " + s.count + " points" +
    (s.step ? " / " + s.step + "s" : "")
  ).join("；");

DATA.series.forEach((s, i) => {
  const el = document.createElement("span");
  const sw = document.createElement("i");

  sw.className = "dot";
  sw.style.background =
    colors[i % colors.length];

  el.append(
    sw,
    document.createTextNode(s.name)
  );

  document.getElementById("legend")
    .appendChild(el);
});

if (DATA.series.length === 1) {
  const s = DATA.series[0];

  [
    ["最新", s.current],
    ["平均", s.average],
    ["最大", s.maximum],
    ["最小", s.minimum]
  ].forEach(([k, v]) => {
    const e = document.createElement("div");
    e.className = "stat";
    e.textContent = k;

    const b = document.createElement("b");
    b.textContent = fmt(v) + " " + DATA.unit;

    e.appendChild(b);
    document.getElementById("stats")
      .appendChild(e);
  });
}

if (DATA.series.some(
  s => s.count > s.displayCount
)) {
  document.getElementById("notice").textContent =
    "部分顯示資料已抽樣；統計使用全部原始樣本。";
}

function resize() {
  const r = plot.getBoundingClientRect();
  const d = Math.min(
    devicePixelRatio || 1,
    2
  );

  const w = Math.max(280, r.width);
  const h = Math.max(240, r.height);

  cvs.width = Math.round(w * d);
  cvs.height = Math.round(h * d);

  ctx.setTransform(d, 0, 0, d, 0, 0);

  return {w, h};
}

function all() {
  return DATA.series.flatMap(s => s.points);
}

function draw() {
  const {w, h} = resize();

  ctx.clearRect(0, 0, w, h);
  ctx.font = "12px system-ui";
  ctx.textBaseline = "middle";

  if (style === "horizontal") {
    horizontal(w, h);
    height();
    return;
  }

  const p = all();

  if (!p.length) return;

  const b = {
    left:76,
    right:w - 20,
    top:16,
    bottom:h - 47
  };

  const minX = Math.min(
    ...p.map(x => x[0])
  );

  const maxX = Math.max(
    ...p.map(x => x[0])
  );

  const minV = Math.min(
    ...p.map(x => x[1])
  );

  const maxV = Math.max(
    ...p.map(x => x[1])
  );

  const delta = Math.max(
    Math.abs(maxV - minV),
    Math.abs(maxV) * .05,
    0.000001
  );

  const low = zero
    ? Math.min(0, minV)
    : minV - delta * .15;

  const high = zero
    ? Math.max(
        maxV + delta * .1,
        low + 0.000001
      )
    : maxV + delta * .15;

  const sx = t =>
    b.left +
    (t - minX) /
    Math.max(1, maxX - minX) *
    (b.right - b.left);

  const sy = v =>
    b.bottom -
    (v - low) /
    Math.max(0.00000001, high - low) *
    (b.bottom - b.top);

  geometry = {
    b, sx, sy, minX, maxX,
    horizontal:false
  };

  ctx.strokeStyle = "#334155";
  ctx.lineWidth = 1;
  ctx.fillStyle = "#cbd5e1";

  for (let i = 0; i <= 4; i++) {
    const v =
      low + (high - low) * i / 4;

    const y = sy(v);

    ctx.beginPath();
    ctx.moveTo(b.left, y);
    ctx.lineTo(b.right, y);
    ctx.stroke();

    ctx.textAlign = "right";
    ctx.fillText(fmt(v), b.left - 9, y);

    const t =
      minX + (maxX - minX) * i / 4;

    ctx.textAlign = "center";

    ctx.fillText(
      label(t),
      sx(t),
      b.bottom + 19
    );
  }

  DATA.series.forEach((s, i) => {
    const color =
      colors[i % colors.length];

    const pp = s.points;

    ctx.fillStyle = color;
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;

    if (style === "bars") {
      const width = Math.max(
        1,
        Math.min(
          16,
          (b.right - b.left) /
          (
            Math.max(1, pp.length) *
            DATA.series.length
          ) * .8
        )
      );

      pp.forEach(p => {
        const x =
          sx(p[0]) +
          (
            i - (DATA.series.length - 1) / 2
          ) * width;

        const y = sy(p[1]);
        const base = sy(0);

        ctx.globalAlpha = .8;

        ctx.fillRect(
          x - width / 2,
          Math.min(y, base),
          width,
          Math.max(1, Math.abs(y - base))
        );
      });

      ctx.globalAlpha = 1;

    } else {

      if (
        style === "area" &&
        pp.length
      ) {
        ctx.beginPath();
        ctx.moveTo(
          sx(pp[0][0]),
          sy(0)
        );

        pp.forEach(p =>
          ctx.lineTo(
            sx(p[0]),
            sy(p[1])
          )
        );

        ctx.lineTo(
          sx(pp[pp.length - 1][0]),
          sy(0)
        );

        ctx.closePath();
        ctx.globalAlpha = .2;
        ctx.fill();
        ctx.globalAlpha = 1;
      }

      if (style !== "points") {
        ctx.beginPath();

        pp.forEach((p, j) =>
          j
            ? ctx.lineTo(
                sx(p[0]),
                sy(p[1])
              )
            : ctx.moveTo(
                sx(p[0]),
                sy(p[1])
              )
        );

        ctx.stroke();

      } else {
        pp.forEach(p => {
          ctx.beginPath();
          ctx.arc(
            sx(p[0]),
            sy(p[1]),
            3,
            0,
            2 * Math.PI
          );

          ctx.fill();
        });
      }
    }
  });

  if (hover !== null) {
    ctx.strokeStyle = "#cbd5e1";
    ctx.setLineDash([4, 4]);

    ctx.beginPath();
    ctx.moveTo(
      sx(hover),
      b.top
    );
    ctx.lineTo(
      sx(hover),
      b.bottom
    );
    ctx.stroke();

    ctx.setLineDash([]);
  }

  height();
}

function horizontal(w, h) {
  const rows = DATA.series.map(
    (s, i) => ({
      name:s.name,
      value:s.current,
      t:s.latestTimestamp,
      color:colors[i % colors.length]
    })
  ).sort(
    (a, b) => b.value - a.value
  );

  const left = Math.min(
    225,
    w * .43
  );

  const max = Math.max(
    .00001,
    ...rows.map(
      r => Math.abs(r.value)
    )
  );

  const space = Math.max(
    30,
    w - left - 75
  );

  geometry = {
    horizontal:true,
    rows,
    top:17,
    rowH:34
  };

  rows.forEach((r, i) => {
    const y = 17 + i * 34;

    ctx.fillStyle = "#cbd5e1";
    ctx.textAlign = "right";

    ctx.fillText(
      r.name.length > 27
        ? r.name.slice(0, 24) + "..."
        : r.name,
      left - 8,
      y + 12
    );

    ctx.fillStyle = r.color;

    const bar = Math.max(
      1,
      Math.abs(r.value) / max * space
    );

    ctx.fillRect(
      left,
      y + 3,
      bar,
      19
    );

    ctx.fillStyle = "#e2e8f0";
    ctx.textAlign = "left";

    ctx.fillText(
      fmt(r.value),
      Math.min(
        w - 60,
        left + bar + 5
      ),
      y + 12
    );
  });
}

function nearest(points, t) {
  if (!points.length) return null;

  let best = points[0];
  let dist = Math.abs(
    best[0] - t
  );

  for (const p of points) {
    const d = Math.abs(
      p[0] - t
    );

    if (d < dist) {
      best = p;
      dist = d;
    }
  }

  return best;
}

function move(e) {
  if (!geometry) return;

  const r = cvs.getBoundingClientRect();

  const x = e.clientX - r.left;
  const y = e.clientY - r.top;

  let text = "";

  if (geometry.horizontal) {
    const index = Math.floor(
      (y - geometry.top) /
      geometry.rowH
    );

    const row = geometry.rows[index];

    if (!row) {
      tip.style.display = "none";
      return;
    }

    text =
      "<b>" + esc(row.name) + "</b>" +
      "<div>" + fmt(row.value) +
      " " + esc(DATA.unit) + "</div>" +
      "<div>最新樣本：" +
      esc(full(row.t)) + "</div>";

  } else {
    const g = geometry;

    if (
      x < g.b.left ||
      x > g.b.right ||
      y < g.b.top ||
      y > g.b.bottom
    ) {
      tip.style.display = "none";
      hover = null;
      draw();
      return;
    }

    const guess =
      g.minX +
      (x - g.b.left) /
      (g.b.right - g.b.left) *
      (g.maxX - g.minX);

    let best = null;
    let d = Infinity;

    for (const s of DATA.series) {
      const p = nearest(
        s.points,
        guess
      );

      if (
        p &&
        Math.abs(p[0] - guess) < d
      ) {
        d = Math.abs(
          p[0] - guess
        );

        best = p;
      }
    }

    if (!best) return;

    hover = best[0];

    text =
      "<b>" +
      esc(full(hover)) +
      " (UTC+8)</b>";

    DATA.series.forEach((s, i) => {
      const p = nearest(
        s.points,
        hover
      );

      if (!p) return;

      const tol = s.step
        ? Math.max(
            1,
            s.step * .75
          )
        : 1;

      if (
        Math.abs(p[0] - hover) > tol
      ) {
        return;
      }

      text +=
        '<div><i class="dot" style="background:' +
        colors[i % colors.length] +
        '"></i>' +
        esc(s.name) +
        "：<b>" +
        fmt(p[1]) +
        " " +
        esc(DATA.unit) +
        "</b></div>";
    });

    draw();
  }

  tip.innerHTML = text;
  tip.style.display = "block";

  const width =
    tip.offsetWidth || 200;

  const heightTip =
    tip.offsetHeight || 70;

  tip.style.left =
    Math.max(
      2,
      Math.min(
        x + 13,
        r.width - width - 3
      )
    ) + "px";

  tip.style.top =
    Math.max(
      2,
      Math.min(
        y + 12,
        r.height - heightTip - 3
      )
    ) + "px";
}

cvs.addEventListener(
  "pointermove",
  move
);

cvs.addEventListener(
  "pointerleave",
  () => {
    hover = null;
    tip.style.display = "none";
    draw();
  }
);

controls.querySelectorAll("button")
  .forEach(b =>
    b.addEventListener("click", () => {
      if (b.dataset.type === "zoom") {
        zero = !zero;

        b.textContent = zero
          ? "Y 軸：從零"
          : "Y 軸：自動";

        draw();
        return;
      }

      style = b.dataset.type;

      plot.style.height =
        style === "horizontal"
          ? Math.max(
              270,
              DATA.series.length * 34 + 50
            ) + "px"
          : "330px";

      controls
        .querySelectorAll(
          "button[data-type]"
        )
        .forEach(e => {
          if (
            e.dataset.type !== "zoom"
          ) {
            e.classList.toggle(
              "active",
              e.dataset.type === style
            );
          }
        });

      hover = null;
      tip.style.display = "none";
      draw();
    })
  );

const selected = controls.querySelector(
  'button[data-type="' + style + '"]'
);

if (selected) {
  selected.classList.add("active");
}

if (style === "horizontal") {
  plot.style.height =
    Math.max(
      270,
      DATA.series.length * 34 + 50
    ) + "px";
}

window.addEventListener(
  "resize",
  draw
);

window.addEventListener(
  "load",
  draw
);

if (
  typeof ResizeObserver !== "undefined"
) {
  new ResizeObserver(
    height
  ).observe(document.body);
}

draw();
height();

})();
</script>
</body>
</html>
'''

        return template.replace(
            "__PAYLOAD__",
            json_data,
        )

    # ============================================================
    # Grafana Image Support
    # ============================================================

    @staticmethod
    def image_html(title, mime, encoded):
        safe_title = html.escape(
            str(title),
            quote=True,
        )

        return (
            "<!doctype html><html><head>"
            "<meta charset='utf-8'>"
            "<meta name='viewport' "
            "content='width=device-width,initial-scale=1'>"
            "<style>"
            "html,body{padding:0;margin:0;"
            "width:100%;overflow:hidden;"
            "background:transparent}"
            "img{display:block;width:100%;"
            "max-width:100%;height:auto}"
            "</style></head><body>"
            f'<img id="gimg" alt="{safe_title}" '
            f'src="data:{mime};base64,{encoded}">'
            "<script>"
            "(function(){"
            "function resize(){"
            "parent.postMessage({type:'iframe:height',"
            "height:Math.ceil("
            "document.documentElement.scrollHeight"
            ")},'*')}"
            "window.addEventListener('load',resize);"
            "window.addEventListener('resize',resize);"
            "document.getElementById('gimg')"
            ".addEventListener('load',resize);"
            "if(typeof ResizeObserver!=='undefined')"
            "new ResizeObserver(resize)"
            ".observe(document.body);"
            "resize()"
            "})();"
            "</script></body></html>"
        )

    @staticmethod
    def decode_image(item, max_bytes):
        if not isinstance(item, dict):
            return None

        mime = str(
            item.get("mimeType")
            or item.get("mime_type")
            or ""
        ).lower().strip()

        if mime not in (
            "image/png",
            "image/jpeg",
            "image/webp",
        ):
            return None

        encoded = item.get("data")

        if not isinstance(encoded, str):
            return None

        if encoded.startswith("data:"):
            prefix, separator, encoded = (
                encoded.partition(",")
            )

            if (
                not separator
                or not prefix.lower().startswith(
                    "data:" + mime + ";base64"
                )
            ):
                return None

        encoded = "".join(encoded.split())

        if (
            len(encoded)
            > int(max_bytes * 4 / 3) + 8
        ):
            return None

        try:
            binary = base64.b64decode(
                encoded,
                validate=True,
            )

        except (
            ValueError,
            TypeError,
            binascii.Error,
        ):
            return None

        if (
            not binary
            or len(binary) > max_bytes
        ):
            return None

        if (
            mime == "image/png"
            and not binary.startswith(
                b"\x89PNG\r\n\x1a\n"
            )
        ):
            return None

        if (
            mime == "image/jpeg"
            and not binary.startswith(
                b"\xff\xd8\xff"
            )
        ):
            return None

        if (
            mime == "image/webp"
            and not (
                binary[:4] == b"RIFF"
                and binary[8:12] == b"WEBP"
            )
        ):
            return None

        return {
            "mime": mime,
            "encoded": encoded,
            "hash": hashlib.sha256(
                binary
            ).hexdigest(),
        }

    # ============================================================
    # Legacy Grafana Rendering
    # ============================================================

    @staticmethod
    def grafana_time(value, default):
        value = str(value or default)

        if re.fullmatch(
            r"(?:now(?:-\d{1,4}[smhdw])?|\d{13})",
            value,
        ):
            return value

        return default

    def grafana_render_params(self, job):
        tool = job["tool"]
        params = job["params"]

        uid = str(
            params.get("dashboard_uid") or ""
        ).strip()

        if not re.fullmatch(
            r"[A-Za-z0-9_-]{1,80}",
            uid,
        ):
            raise ValueError(
                "Invalid dashboard_uid"
            )

        query = {
            "orgId": self.valves.GRAFANA_ORG_ID,
            "from": self.grafana_time(
                params.get("from_time"),
                "now-1h",
            ),
            "to": self.grafana_time(
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

        if tool == "grafana_render_panel":
            panel = int(
                params.get("panel_id") or 0
            )

            if panel <= 0 or panel > 100000:
                raise ValueError(
                    "Invalid panel_id"
                )

            query["panelId"] = panel

            path = (
                f"render/d-solo/"
                f"{quote(uid, safe='')}/_"
            )

            title = f"Grafana Panel {panel}"

        elif tool == "grafana_render_dashboard":
            path = (
                f"render/d/"
                f"{quote(uid, safe='')}/_"
            )
            title = "Grafana Dashboard"

        else:
            raise ValueError(
                "Unsupported tool"
            )

        variables = params.get("variables")

        if isinstance(variables, str):
            for pair in variables.split(";")[:12]:
                if "=" not in pair:
                    continue

                key, val = (
                    v.strip()
                    for v in pair.split("=", 1)
                )

                if (
                    re.fullmatch(
                        r"var-[\w-]{1,64}",
                        key,
                    )
                    and re.fullmatch(
                        r"[\w.:/ -]{1,128}",
                        val,
                    )
                ):
                    query[key] = val

        tz = str(
            params.get("timezone") or ""
        )

        if re.fullmatch(
            r"[\w+./-]{1,64}",
            tz,
        ):
            query["tz"] = tz

        return title, path, query

    # ============================================================
    # Main Open WebUI Pipe
    # ============================================================

    async def pipe(
        self,
        body: dict,
        __user__: dict = None,
        __event_emitter__=None,
        __task__=None,
    ):

        # Admin-only security
        if (
            not __user__
            or __user__.get("role") != "admin"
        ):
            return (
                "Access denied: "
                "administrator access required."
            )

        if __task__:
            return "HolmesGPT SRE"

        messages = body.get("messages") or []

        questions = [
            m.get("content")
            for m in messages
            if (
                m.get("role") == "user"
                and isinstance(
                    m.get("content"),
                    str,
                )
            )
        ]

        if (
            not questions
            or not questions[-1].strip()
        ):
            return (
                "請輸入 Kubernetes 維運問題。"
            )

        question = questions[-1].strip()

        system_prompt = "\n\n".join(
            m.get("content", "")
            for m in messages
            if (
                m.get("role") == "system"
                and isinstance(
                    m.get("content"),
                    str,
                )
            )
        )

        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

        if self.valves.API_KEY:
            headers["X-API-Key"] = (
                self.valves.API_KEY
            )

        request_payload = {
            "model": self.valves.MODEL,
            "ask": question,
            "stream": True,
            "enable_tool_approval": False,
            "additional_system_prompt": system_prompt,
        }

        url = (
            self.valves.HOLMES_URL.rstrip("/")
            + "/api/chat"
        )

        # Start total investigation timer
        start = time.perf_counter()

        history = []
        params_by_id = {}
        prom_jobs = {}
        grafana_jobs = []
        direct_images = []

        pending_starts = {}
        seen_images = set()
        seen_grafana = set()

        final_answer = None
        pending_event = ""
        pending_lines = []

        emit_errors = []
        server_errors = []

        # --------------------------------------------------------
        # Event emitter utilities
        # --------------------------------------------------------

        async def status(
            message,
            done=False,
        ):
            if __event_emitter__:
                await __event_emitter__({
                    "type": "status",
                    "data": {
                        "description": message,
                        "done": done,
                        "hidden": False,
                    },
                })

        async def emit_embed(content):
            if __event_emitter__:
                await __event_emitter__({
                    "type": "embeds",
                    "data": {
                        "embeds": [content],
                    },
                })

        # --------------------------------------------------------
        # HolmesGPT SSE event handler
        # --------------------------------------------------------

        async def handle_event(
            event_type,
            event,
        ):
            nonlocal final_answer

            if event_type == "start_tool_calling":

                name = str(
                    event.get("tool_name")
                    or event.get("name")
                    or "unknown"
                )

                call_id = (
                    event.get("id")
                    or event.get("tool_call_id")
                )

                params = (
                    event.get("params")
                    or event.get("parameters")
                    or {}
                )

                if (
                    call_id
                    and isinstance(params, dict)
                ):
                    params_by_id[call_id] = params

                record = {
                    "tool": name,
                    "call_id": call_id,
                    "status": "running",
                    "message": "執行中",
                    "elapsed": None,
                    "server_elapsed": None,
                }

                history.append(record)

                if call_id:
                    pending_starts[call_id] = (
                        time.perf_counter(),
                        record,
                    )
                else:
                    record["started"] = (
                        time.perf_counter()
                    )

                await status(
                    f"正在執行 {name}..."
                )

                return

            if event_type == "tool_calling_result":

                name = str(
                    event.get("name")
                    or event.get("tool_name")
                    or "unknown"
                )

                call_id = (
                    event.get("tool_call_id")
                    or event.get("id")
                )

                result = self.as_dict(
                    event.get("result")
                )

                params = (
                    result.get("params")
                    or event.get("params")
                    or params_by_id.get(call_id)
                    or {}
                )

                if not isinstance(params, dict):
                    params = {}

                outcome = str(
                    result.get("status")
                    or "unknown"
                ).lower()

                error = str(
                    result.get("error") or ""
                )

                raw_data = self.as_dict(
                    result.get("data")
                )

                stderr = str(
                    raw_data.get("stderr") or ""
                )

                # Detect errors hidden in successful responses
                if (
                    "runtime: out of memory"
                    in stderr.lower()
                    or "fatal error: out of memory"
                    in stderr.lower()
                    or "unable to find resource kind"
                    in stderr.lower()
                ):
                    outcome = "error"
                    error = (
                        error
                        or stderr.splitlines()[0]
                    )

                if outcome == "success":
                    message = (
                        f"{name} 執行成功"
                    )
                else:
                    message = (
                        f"{name} 失敗："
                        f"{(error or stderr or 'unknown')[:150]}"
                    )

                now = time.perf_counter()
                record = None

                # Match concurrent tool calls by call ID
                if (
                    call_id
                    and call_id in pending_starts
                ):
                    started, record = (
                        pending_starts.pop(call_id)
                    )

                else:
                    started = None

                    if call_id:
                        for row in reversed(history):
                            if (
                                row["status"] == "running"
                                and row["call_id"]
                                == call_id
                            ):
                                record = row
                                started = (
                                    row.get("started")
                                )
                                break

                    else:
                        for row in reversed(history):
                            if (
                                row["status"] == "running"
                                and row["tool"] == name
                            ):
                                record = row
                                started = (
                                    row.get("started")
                                )
                                break

                if record is None:
                    record = {
                        "tool": name,
                        "call_id": call_id,
                        "status": "running",
                        "message": "",
                        "elapsed": None,
                        "server_elapsed": None,
                    }

                    history.append(record)

                record.update(
                    status=outcome,
                    message=message,
                )

                if started is not None:
                    record["elapsed"] = max(
                        0.0,
                        now - started,
                    )

                # HolmesGPT internal execution time
                try:
                    v = float(
                        result.get("elapsed_seconds")
                    )

                    if (
                        math.isfinite(v)
                        and v >= 0
                    ):
                        record["server_elapsed"] = v

                except (
                    ValueError,
                    TypeError,
                    OverflowError,
                ):
                    pass

                # ----------------------------------------------
                # Grafana MCP direct image
                # ----------------------------------------------

                if (
                    self.valves.SHOW_RENDERED_IMAGES
                    and outcome == "success"
                    and name in (
                        "get_panel_image",
                        "get_dashboard_image",
                    )
                ):

                    images = (
                        result.get("images") or []
                    )

                    if isinstance(images, list):
                        for item in images:
                            decoded = (
                                self.decode_image(
                                    item,
                                    self.valves.MAX_IMAGE_BYTES,
                                )
                            )

                            if (
                                not decoded
                                or decoded["hash"]
                                in seen_images
                                or len(direct_images)
                                >= self.valves.MAX_IMAGES
                            ):
                                continue

                            seen_images.add(
                                decoded["hash"]
                            )

                            direct_images.append(
                                decoded
                            )

                # ----------------------------------------------
                # Prometheus range query
                # ----------------------------------------------

                if (
                    self.valves.SHOW_PROMETHEUS_CHARTS
                    and outcome == "success"
                    and call_id
                    and name
                    == "execute_prometheus_range_query"
                ):
                    matrix = (
                        self.matrix_result(result)
                    )

                    if (
                        matrix is not None
                        and len(prom_jobs)
                        < self.valves.MAX_CHARTS
                    ):
                        prom_jobs[call_id] = {
                            "params": dict(params),
                            "matrix": matrix,
                        }

                # ----------------------------------------------
                # Legacy Grafana renderer jobs
                # ----------------------------------------------

                if (
                    self.valves.SHOW_RENDERED_IMAGES
                    and outcome == "success"
                    and name in (
                        "grafana_render_panel",
                        "grafana_render_dashboard",
                    )
                ):
                    fingerprint = json.dumps(
                        {
                            "tool": name,
                            "params": params,
                        },
                        sort_keys=True,
                        ensure_ascii=False,
                    )

                    if (
                        fingerprint not in seen_grafana
                        and len(grafana_jobs)
                        < self.valves.MAX_IMAGES
                    ):
                        seen_grafana.add(
                            fingerprint
                        )

                        grafana_jobs.append({
                            "tool": name,
                            "params": dict(params),
                        })

                await status(message)
                return

            if event_type == "ai_message":
                if event.get("content"):
                    await status(
                        "AI 正在分析調查結果..."
                    )

            elif event_type == "ai_answer_end":
                final_answer = (
                    event.get("analysis")
                    or event.get("answer")
                    or ""
                )

                await status(
                    "AI 分析完成，正在整理圖表與統計..."
                )

            elif event_type == "error":
                detail = str(
                    event.get("message")
                    or event.get("error")
                    or "unknown"
                )[:180]

                server_errors.append(
                    detail
                )

                await status(
                    "HolmesGPT 錯誤：" + detail
                )

        # --------------------------------------------------------
        # SSE parser
        # --------------------------------------------------------

        async def flush_sse():
            nonlocal pending_event
            nonlocal pending_lines

            if not pending_lines:
                pending_event = ""
                return

            raw = "\n".join(
                pending_lines
            )

            event_type = (
                pending_event or "message"
            )

            pending_event = ""
            pending_lines = []

            if raw == "[DONE]":
                return

            try:
                data = json.loads(raw)

            except (
                ValueError,
                TypeError,
            ):
                return

            if isinstance(data, dict):
                await handle_event(
                    event_type,
                    data,
                )

        # --------------------------------------------------------
        # Timing statistics
        # --------------------------------------------------------

        def timing_text():
            elapsed = (
                time.perf_counter() - start
            )

            complete = [
                r
                for r in history
                if r["status"] != "running"
            ]

            success = sum(
                r["status"] == "success"
                for r in complete
            )

            failed = sum(
                r["status"] != "success"
                for r in complete
            )

            lines = [
                "",
                "---",
                "### 調查執行統計",
                (
                    f"- 總耗時：**{elapsed:.2f} 秒**"
                    "（含 HolmesGPT、工具及圖片處理）"
                ),
                (
                    f"- 工具呼叫：**{len(history)} 次**；"
                    f"成功 **{success}**、"
                    f"失敗或未知 **{failed}**、"
                    f"未完成 **{len(history) - len(complete)}**"
                ),
                "",
                "| 工具 | 狀態 | 內部耗時 | 事件間隔 |",
                "|---|---|---:|---:|",
            ]

            for r in history[:80]:
                if (
                    r.get("server_elapsed")
                    is not None
                ):
                    internal = (
                        f"{r['server_elapsed']:.2f}s"
                    )
                else:
                    internal = "—"

                if (
                    r.get("elapsed")
                    is not None
                ):
                    interval = (
                        f"{r['elapsed']:.2f}s"
                    )
                else:
                    interval = "—"

                tool_name = (
                    r["tool"].replace("|", "/")
                )

                lines.append(
                    f"| `{tool_name}` | "
                    f"{r['status']} | "
                    f"{internal} | "
                    f"{interval} |"
                )

            lines.append(
                "\n*工具可能平行執行，因此各項耗時不能相加"
                "作為總時間。事件間隔包含 SSE 傳輸及"
                "排程延遲。*"
            )

            return "\n".join(lines)

        # --------------------------------------------------------
        # HolmesGPT API request
        # --------------------------------------------------------

        try:
            await status(
                "HolmesGPT 正在開始調查"
            )

            timeout = httpx.Timeout(
                float(self.valves.TIMEOUT),
                connect=10.0,
            )

            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
            ) as client:

                async with client.stream(
                    "POST",
                    url,
                    headers=headers,
                    json=request_payload,
                ) as response:

                    response.raise_for_status()

                    async for line in (
                        response.aiter_lines()
                    ):

                        if not line.strip():
                            await flush_sse()
                            continue

                        if line.startswith(":"):
                            continue

                        if line.startswith("event:"):
                            pending_event = (
                                line[6:].strip()
                            )

                        elif line.startswith("data:"):
                            pending_lines.append(
                                line[5:].lstrip()
                            )

                    await flush_sse()

            # ----------------------------------------------------
            # Final answer
            # ----------------------------------------------------

            if self.valves.REMOVE_TOOL_IMAGE_REFERENCES:
                output = self.remove_tool_images(
                    final_answer
                )
            else:
                output = str(
                    final_answer or ""
                )

            if not output:
                output = (
                    "HolmesGPT 未回傳完整調查結果。"
                )

                if server_errors:
                    output += (
                        "\n\n錯誤："
                        + "；".join(
                            server_errors[:3]
                        )
                    )

            # ----------------------------------------------------
            # PromQL markers
            # ----------------------------------------------------

            used = []

            def replace_marker(match):
                try:
                    marker = json.loads(
                        match.group(1)
                    )

                except ValueError:
                    return match.group(0)

                if (
                    marker.get("type")
                    != "promql"
                ):
                    return match.group(0)

                call_id = (
                    marker.get("tool_call_id")
                )

                if call_id in prom_jobs:
                    used.append(
                        call_id
                    )

                    return (
                        "（互動式趨勢圖附於回答中）"
                    )

                return (
                    "（查詢未產生有效時間序列，"
                    "無法顯示圖表）"
                )

            output = re.sub(
                r"<<\s*(\{.*?\})\s*>>",
                replace_marker,
                output,
                flags=re.S,
            )

            # ----------------------------------------------------
            # Prometheus charts
            # ----------------------------------------------------

            rendered = 0

            if (
                self.valves.SHOW_PROMETHEUS_CHARTS
                and __event_emitter__
            ):
                ordered = list(
                    dict.fromkeys(
                        used + list(prom_jobs)
                    )
                )

                for call_id in ordered[
                    : self.valves.MAX_CHARTS
                ]:
                    try:
                        chart = self.chart_payload(
                            prom_jobs[call_id],
                            question,
                        )

                        if chart:
                            await emit_embed(
                                self.chart_html(chart)
                            )

                            rendered += 1

                    except (
                        ValueError,
                        TypeError,
                        OverflowError,
                    ) as exc:
                        emit_errors.append(
                            "Prometheus 圖表："
                            + type(exc).__name__
                        )

            # ----------------------------------------------------
            # Grafana direct images
            # ----------------------------------------------------

            image_count = 0

            if (
                self.valves.SHOW_RENDERED_IMAGES
                and __event_emitter__
            ):
                for item in direct_images:
                    await emit_embed(
                        self.image_html(
                            "Grafana MCP 圖片",
                            item["mime"],
                            item["encoded"],
                        )
                    )

                    image_count += 1

            # ----------------------------------------------------
            # Legacy Grafana re-render
            # ----------------------------------------------------

            if (
                self.valves.SHOW_RENDERED_IMAGES
                and __event_emitter__
                and self.valves.GRAFANA_TOKEN
                and image_count
                < self.valves.MAX_IMAGES
                and grafana_jobs
            ):
                render_headers = {
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
                ) as client:

                    for job in grafana_jobs[
                        :self.valves.MAX_IMAGES - image_count
                    ]:
                        try:
                            title, path, query = (
                                self.grafana_render_params(
                                    job
                                )
                            )

                            response = await client.get(
                                self.valves.GRAFANA_URL.rstrip(
                                    "/"
                                ) + "/" + path,
                                headers=render_headers,
                                params=query,
                            )

                            response.raise_for_status()

                            png = response.content

                            if (
                                response.headers.get(
                                    "Content-Type",
                                    "",
                                )
                                .split(";")[0]
                                .strip()
                                .lower()
                                != "image/png"
                            ):
                                raise ValueError(
                                    "Response is not PNG"
                                )

                            if (
                                len(png)
                                > self.valves.MAX_IMAGE_BYTES
                                or not png.startswith(
                                    b"\x89PNG\r\n\x1a\n"
                                )
                            ):
                                raise ValueError(
                                    "Invalid or oversized PNG"
                                )

                            fingerprint = (
                                hashlib.sha256(
                                    png
                                ).hexdigest()
                            )

                            if fingerprint in seen_images:
                                continue

                            seen_images.add(
                                fingerprint
                            )

                            await emit_embed(
                                self.image_html(
                                    title,
                                    "image/png",
                                    base64.b64encode(
                                        png
                                    ).decode("ascii"),
                                )
                            )

                            image_count += 1

                        except (
                            httpx.HTTPError,
                            ValueError,
                            TypeError,
                            OverflowError,
                        ) as exc:
                            emit_errors.append(
                                "Grafana 渲染："
                                + type(exc).__name__
                            )

            # ----------------------------------------------------
            # Final report
            # ----------------------------------------------------

            if rendered:
                output += (
                    f"\n\n已附上 {rendered} 張 "
                    "Prometheus 互動式圖表。"
                )

            if image_count:
                output += (
                    f"\n\n已附上 {image_count} 張 "
                    "Grafana 圖片。"
                )

            if (
                self.valves.SHOW_TOOL_HISTORY
                and history
            ):
                lines = [
                    "",
                    "---",
                    "### 工具執行紀錄",
                ]

                for i, r in enumerate(
                    history[:60],
                    1,
                ):
                    icon = (
                        "✓"
                        if r["status"] == "success"
                        else "!"
                    )

                    lines.append(
                        f"{i}. {icon} "
                        f"`{r['tool']}` — "
                        f"{r['message']}"
                    )

                output += "\n".join(lines)

            if emit_errors:
                output += (
                    "\n\n> 顯示圖表或圖片時發生問題："
                    + "；".join(
                        emit_errors[:5]
                    )
                )

            if self.valves.SHOW_TIMINGS:
                output += timing_text()

            await status(
                "調查完成，耗時 "
                f"{time.perf_counter() - start:.2f} 秒",
                done=True,
            )

            return output

        # --------------------------------------------------------
        # Error handling
        # --------------------------------------------------------

        except (
            httpx.TimeoutException,
            httpx.HTTPStatusError,
            httpx.RequestError,
        ) as exc:

            if isinstance(
                exc,
                httpx.TimeoutException,
            ):
                error = (
                    "HolmesGPT 請求或串流等待逾時"
                )

            elif isinstance(
                exc,
                httpx.HTTPStatusError,
            ):
                error = (
                    "HolmesGPT HTTP 錯誤："
                    f"{exc.response.status_code}"
                )

            else:
                error = (
                    "HolmesGPT 連線失敗"
                )

            if self.valves.SHOW_TIMINGS:
                error += timing_text()

            await status(
                error.split("\n")[0],
                done=True,
            )

            return error

        except Exception as exc:
            error = (
                "Pipe 處理錯誤："
                + type(exc).__name__
            )

            if self.valves.SHOW_TIMINGS:
                error += timing_text()

            await status(
                "Pipe 執行失敗",
                done=True,
            )

            return error
