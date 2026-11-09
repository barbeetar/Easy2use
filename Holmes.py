
"""
title: HolmesGPT SRE Interactive
description: HolmesGPT Kubernetes SRE with interactive Prometheus charts and Grafana rendering
version: 5.1.0
"""

import base64
import html
import json
import math
import re

from datetime import datetime, timezone
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field


class Pipe:

    class Valves(BaseModel):

        HOLMES_URL: str = Field(
            default="http://holmesgpt-holmes.holmesgpt.svc.cluster.local:80",
            description="HolmesGPT internal API URL",
        )

        MODEL: str = Field(
            default="gemma-local",
        )

        API_KEY: str = Field(
            default="",
            description="Optional HolmesGPT API key",
            json_schema_extra={"input": {"type": "password"}},
        )

        TIMEOUT: int = Field(
            default=300,
            ge=30,
            le=900,
        )

        SHOW_TOOL_HISTORY: bool = Field(default=True)

        SHOW_PROMETHEUS_CHARTS: bool = Field(default=True)

        SHOW_RENDERED_IMAGES: bool = Field(default=True)

        REMOVE_TOOL_IMAGE_REFERENCES: bool = Field(default=True)

        MAX_CHARTS: int = Field(
            default=3,
            ge=1,
            le=5,
        )

        MAX_SERIES: int = Field(
            default=12,
            ge=1,
            le=20,
        )

        MAX_POINTS_PER_SERIES: int = Field(
            default=240,
            ge=20,
            le=500,
        )

        GRAFANA_URL: str = Field(
            default="http://grafana.monitoring.svc.cluster.local:80",
        )

        GRAFANA_TOKEN: str = Field(
            default="",
            json_schema_extra={"input": {"type": "password"}},
        )

        GRAFANA_ORG_ID: int = Field(default=1)

        MAX_IMAGES: int = Field(
            default=2,
            ge=1,
            le=4,
        )

        MAX_IMAGE_BYTES: int = Field(
            default=1500000,
        )

    def __init__(self):
        self.name = "HolmesGPT SRE Interactive"
        self.valves = self.Valves()

    # =========================================================
    # General utilities
    # =========================================================

    @staticmethod
    def remove_tool_image_refs(value):

        text = str(value or "")

        text = re.sub(
            r"!\[([^\]]*)\]\(\s*tool-image://[^)\s]+\s*\)",
            lambda match: (
                "\n\n*" + match.group(1) + "*\n\n"
                if match.group(1).strip()
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

    @staticmethod
    def parse_object(value):

        if isinstance(value, dict):
            return value

        if isinstance(value, str):
            try:
                result = json.loads(value)
                if isinstance(result, dict):
                    return result
            except (ValueError, TypeError):
                pass

        return {}

    @staticmethod
    def parse_timestamp(value):

        if isinstance(value, (int, float)):
            number = float(value)
            if number > 1e11:
                number /= 1000
            return number

        if not value:
            return None

        try:
            value = str(value).strip()

            if value.isdigit():
                return Pipe.parse_timestamp(float(value))

            return datetime.fromisoformat(
                value.replace("Z", "+00:00")
            ).timestamp()

        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def compact_points(points, maximum):

        if len(points) <= maximum:
            return points

        # Preserve first and last points with even sampling.
        # This is visualization downsampling, not aggregation.
        indexes = [
            round(i * (len(points) - 1) / (maximum - 1))
            for i in range(maximum)
        ]

        return [points[i] for i in sorted(set(indexes))]

    # =========================================================
    # Prometheus result parsing
    # =========================================================

    @staticmethod
    def decode_prometheus_matrix(result):

        data = result.get("data")

        for _ in range(4):

            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except (ValueError, TypeError):
                    return None

            if not isinstance(data, dict):
                return None

            if (
                data.get("status") == "error"
                or data.get("error_message")
            ):
                return None

            if data.get("resultType") == "matrix":
                values = data.get("result")
                if isinstance(values, list):
                    return data

            data = data.get("data")

        return None

    @staticmethod
    def determine_unit(params, raw_max):

        output_type = str(
            params.get("output_type") or ""
        ).lower()

        query = str(
            params.get("query") or ""
        ).lower()

        description = str(
            params.get("description") or ""
        ).lower()

        if (
            "memory" in output_type
            or "byte" in output_type
            or "memory" in query
            or "bytes" in query
        ):

            if raw_max >= 1024 ** 3:
                return "GiB", float(1024 ** 3)

            return "MiB", float(1024 ** 2)

        if (
            "cpu" in output_type
            or "cpu" in query
            or "cpu" in description
        ):
            return "CPU cores", 1.0

        if (
            "percent" in output_type
            or "percentage" in output_type
        ):
            return "%", 1.0

        return "Value", 1.0

    @staticmethod
    def metric_name(metric, index):

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

        labels = [
            f"{k}={v}"
            for k, v in sorted(metric.items())
            if k != "__name__"
        ]

        if labels:
            return ", ".join(labels)[:160]

        return f"Series {index + 1}"

    def make_chart_data(self, job, question):

        matrix = job.get("matrix") or {}
        params = job.get("params") or {}

        start = self.parse_timestamp(
            params.get("start")
        )

        end = self.parse_timestamp(
            params.get("end")
        )

        if (
            start is not None
            and end is not None
            and start >= end
        ):
            return None

        parsed_series = []
        raw_max = 0.0

        for index, entry in enumerate(
            matrix.get("result", [])[:self.valves.MAX_SERIES]
        ):

            if not isinstance(entry, dict):
                continue

            metric = entry.get("metric") or {}

            if not isinstance(metric, dict):
                metric = {}

            original_points = []

            for pair in entry.get("values", []):

                if (
                    not isinstance(pair, (list, tuple))
                    or len(pair) < 2
                ):
                    continue

                try:
                    t = float(pair[0])
                    v = float(pair[1])

                    if not (
                        math.isfinite(t)
                        and math.isfinite(v)
                    ):
                        continue

                    if start is not None and t < start - 1:
                        continue

                    if end is not None and t > end + 1:
                        continue

                    original_points.append([t, v])

                except (ValueError, TypeError, OverflowError):
                    continue

            original_points.sort(key=lambda p: p[0])

            # Remove duplicate timestamps.
            deduped = {}

            for t, v in original_points:
                deduped[t] = v

            original_points = [
                [t, v]
                for t, v in sorted(deduped.items())
            ]

            if not original_points:
                continue

            raw_max = max(
                raw_max,
                max(abs(p[1]) for p in original_points),
            )

            parsed_series.append({
                "name": self.metric_name(metric, index),
                "originalCount": len(original_points),
                "rawPoints": original_points,
            })

        if not parsed_series:
            return None

        unit, divisor = self.determine_unit(
            params,
            raw_max,
        )

        series = []

        for s in parsed_series:

            points = self.compact_points(
                s["rawPoints"],
                self.valves.MAX_POINTS_PER_SERIES,
            )

            converted = [
                [t, v / divisor]
                for t, v in points
            ]

            full_values = [
                v / divisor
                for _, v in s["rawPoints"]
            ]

            series.append({
                "name": s["name"],
                "points": converted,
                "originalCount": s["originalCount"],
                "current": full_values[-1],
                "average": sum(full_values) / len(full_values),
                "maximum": max(full_values),
                "minimum": min(full_values),
            })

        actual_start = min(
            s["rawPoints"][0][0]
            for s in parsed_series
        )

        actual_end = max(
            s["rawPoints"][-1][0]
            for s in parsed_series
        )

        question_lower = str(question).lower()

        # Horizontal Bar compares the latest value of
        # several series. Time Bars represent time samples.
        comparison_terms = (
            "比較", "各pod", "各 pod",
            "排名", "排行", "top",
            "compare", "ranking",
        )

        horizontal_terms = (
            "bar chart", "長條圖", "橫條圖",
            "horizontal bar",
        )

        if (
            any(t in question_lower for t in horizontal_terms)
            and (
                len(series) > 1
                or any(t in question_lower for t in comparison_terms)
            )
        ):
            default_style = "horizontal"
        elif (
            "柱狀圖" in question_lower
            or "時間柱" in question_lower
            or "bars" in question_lower
        ):
            default_style = "bars"
        elif (
            "面積圖" in question_lower
            or "area chart" in question_lower
        ):
            default_style = "area"
        elif (
            "散點" in question_lower
            or "points" in question_lower
        ):
            default_style = "points"
        elif any(t in question_lower for t in horizontal_terms):
            default_style = "bars"
        else:
            default_style = "line"

        return {
            "title": str(
                params.get("description")
                or "Prometheus Trend"
            )[:240],
            "query": str(params.get("query") or "")[:4000],
            "unit": unit,
            "defaultStyle": default_style,
            "queryStart": start,
            "queryEnd": end,
            "actualStart": actual_start,
            "actualEnd": actual_end,
            "series": series,
        }

    # =========================================================
    # Interactive HTML chart
    # =========================================================

    @staticmethod
    def chart_html(payload):

        # Escape unsafe characters in JSON embedded in HTML.
        data_json = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )

        data_json = (
            data_json
            .replace("&", "\\u0026")
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
        )

        template = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{box-sizing:border-box}
html,body{
 margin:0;padding:0;overflow:hidden;width:100%;
 font-family:system-ui,-apple-system,sans-serif;
 color:#e2e8f0;background:transparent
}
.card{
 background:#101827;border:1px solid #334155;
 border-radius:12px;padding:16px;width:100%;
}
.header{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap}
.title{font-size:16px;font-weight:650;overflow-wrap:anywhere}
.subtitle{font-size:12px;color:#9ca3af;margin-top:4px}
.controls{display:flex;gap:5px;flex-wrap:wrap;margin:12px 0}
.controls button{
 background:#253349;border:1px solid #45556b;
 border-radius:7px;color:#e2e8f0;
 padding:7px 11px;cursor:pointer;font-size:12px
}
.controls button.active{
 background:#155e75;border-color:#22d3ee;color:white
}
.plot{
 position:relative;width:100%;height:330px;
 touch-action:pan-y
}
canvas{width:100%;height:100%;display:block}
.tooltip{
 position:absolute;z-index:10;pointer-events:none;
 display:none;background:#020617ee;
 border:1px solid #64748b;border-radius:8px;
 padding:10px;font-size:12px;max-width:280px;
 box-shadow:0 8px 20px #0007;
}
.tooltip .dt{color:#cbd5e1;font-weight:600;margin-bottom:6px}
.tooltip .item{display:flex;gap:8px;justify-content:space-between}
.legend{display:flex;gap:12px;flex-wrap:wrap;margin-top:10px}
.legend span{font-size:12px;overflow-wrap:anywhere}
.swatch{
 display:inline-block;width:10px;height:10px;
 border-radius:3px;margin-right:5px
}
.stats{
 display:flex;gap:12px;flex-wrap:wrap;
 font-size:12px;color:#cbd5e1;margin-top:12px
}
.stat{
 background:#1e293b;padding:8px 10px;
 border-radius:7px;min-width:100px
}
.stat b{display:block;font-size:15px;color:#f8fafc;margin-top:4px}
details{margin-top:12px;font-size:12px;color:#94a3b8}
pre{white-space:pre-wrap;overflow-wrap:anywhere}
.notice{font-size:12px;color:#fbbf24;margin-top:8px}
</style>
</head>
<body>
<div class="card">
 <div class="header">
  <div>
   <div class="title" id="title"></div>
   <div class="subtitle" id="subtitle"></div>
  </div>
 </div>
 <div class="controls" id="controls">
  <button data-style="line">Line 折線</button>
  <button data-style="area">Area 面積</button>
  <button data-style="bars">Bars 柱狀</button>
  <button data-style="points">Points 散點</button>
  <button data-style="horizontal">Bar 比較</button>
 </div>
 <div class="plot" id="plot">
  <canvas id="chart"></canvas>
  <div class="tooltip" id="tooltip"></div>
 </div>
 <div class="legend" id="legend"></div>
 <div class="stats" id="stats"></div>
 <div class="notice" id="notice"></div>
 <details>
  <summary>查看實際 PromQL</summary>
  <pre id="promql"></pre>
 </details>
</div>
<script>
(function(){
"use strict";

const DATA = __PAYLOAD__;
const colors=[
 "#22d3ee","#34d399","#fbbf24","#c084fc",
 "#fb7185","#60a5fa","#f97316","#a3e635",
 "#f472b6","#2dd4bf","#eab308","#818cf8",
 "#f87171","#38bdf8","#4ade80","#facc15",
 "#a78bfa","#fda4af","#67e8f9","#d8b4fe"
];

const canvas=document.getElementById("chart");
const plot=document.getElementById("plot");
const tooltip=document.getElementById("tooltip");
const controls=document.getElementById("controls");
const ctx=canvas.getContext("2d");
let style=DATA.defaultStyle||"line";
let geometry=null;
let hover=null;

const tzFormatter=new Intl.DateTimeFormat("zh-TW",{
 timeZone:"Asia/Taipei",
 year:"numeric",month:"2-digit",day:"2-digit",
 hour:"2-digit",minute:"2-digit",second:"2-digit",
 hour12:false,hourCycle:"h23"
});

const shortFormatter=new Intl.DateTimeFormat("zh-TW",{
 timeZone:"Asia/Taipei",
 hour:"2-digit",minute:"2-digit",
 hour12:false,hourCycle:"h23"
});

function fullTime(t){
 return tzFormatter.format(new Date(t*1000));
}
function shortTime(t){
 return shortFormatter.format(new Date(t*1000));
}
function fmt(v){
 if(!Number.isFinite(v))return "N/A";
 const a=Math.abs(v);
 if(a>=1000)return v.toLocaleString("en-US",{maximumFractionDigits:2});
 if(a>=1)return v.toFixed(3);
 return v.toFixed(5);
}
function escapeHtml(s){
 return String(s).replace(/[&<>"']/g,c=>({
  "&":"&amp;","<":"&lt;",">":"&gt;",
  '"':"&quot;","'":"&#39;"
 })[c]);
}
function reportHeight(){
 parent.postMessage({
  type:"iframe:height",
  height:Math.ceil(document.documentElement.scrollHeight)
 },"*");
}
function allPoints(){
 return DATA.series.flatMap(s=>s.points);
}

document.getElementById("title").textContent=DATA.title;
document.getElementById("promql").textContent=DATA.query;

const duration=(DATA.actualEnd-DATA.actualStart)/60;
document.getElementById("subtitle").textContent=
 "單位："+DATA.unit+
 " · 台灣時間 UTC+8 · 實際資料："+
 fullTime(DATA.actualStart)+" ～ "+fullTime(DATA.actualEnd)+
 " · 約 "+duration.toFixed(1)+" 分鐘";

let legend=document.getElementById("legend");

DATA.series.forEach((s,i)=>{
 let item=document.createElement("span");
 let swatch=document.createElement("span");
 swatch.className="swatch";
 swatch.style.background=colors[i%colors.length];
 item.appendChild(swatch);
 item.appendChild(document.createTextNode(s.name));
 legend.appendChild(item);
});

const stats=document.getElementById("stats");
if(DATA.series.length===1){
 const s=DATA.series[0];
 [
  ["最新樣本",s.current],
  ["樣本平均",s.average],
  ["樣本最大",s.maximum],
  ["樣本最小",s.minimum]
 ].forEach(([label,value])=>{
  const el=document.createElement("div");
  el.className="stat";
  const heading=document.createElement("span");
  heading.textContent=label;
  const num=document.createElement("b");
  num.textContent=fmt(value)+" "+DATA.unit;
  el.append(heading,num);
  stats.appendChild(el);
 });
}

const notice=document.getElementById("notice");

if(DATA.queryStart!=null&&DATA.queryEnd!=null){
 const requested=DATA.queryEnd-DATA.queryStart;
 const actual=DATA.actualEnd-DATA.actualStart;
 if(requested>0&&actual<requested*0.75){
  notice.textContent=
   "注意：實際資料僅涵蓋部分查詢時間範圍，請確認指標是否缺漏。";
 }
}
const sampled=DATA.series.some(
 s=>s.originalCount>s.points.length
);
if(sampled){
 notice.textContent+=
  (notice.textContent?" ":"")+
  "圖形已對顯示點數抽樣；統計值使用抽樣前的資料。";
}

function resizeCanvas(){
 const rect=plot.getBoundingClientRect();
 const dpr=Math.min(window.devicePixelRatio||1,2);
 const w=Math.max(320,rect.width);
 const h=Math.max(250,rect.height);
 canvas.width=Math.round(w*dpr);
 canvas.height=Math.round(h*dpr);
 ctx.setTransform(dpr,0,0,dpr,0,0);
 return {w,h};
}

function plotBounds(w,h){
 return {
  left:74,right:w-22,top:18,bottom:h-50
 };
}

function draw(){
 const {w,h}=resizeCanvas();
 ctx.clearRect(0,0,w,h);
 ctx.font="12px system-ui";
 ctx.textBaseline="middle";

 const b=plotBounds(w,h);
 const points=allPoints();

 if(!points.length)return;

 const horizontal=style==="horizontal";

 if(horizontal){
  drawHorizontal(w,h);
  reportHeight();
  return;
 }

 const minX=Math.min(...points.map(p=>p[0]));
 const maxX=Math.max(...points.map(p=>p[0]));
 const minV=Math.min(...points.map(p=>p[1]));
 const maxV=Math.max(...points.map(p=>p[1]));

 const low=Math.min(0,minV);
 const hi=Math.max(maxV,low+0.000001);
 const span=hi-low;
 const yTop=hi+span*0.12;

 const sx=t=>b.left+
  (t-minX)/Math.max(1,maxX-minX)*(b.right-b.left);

 const sy=v=>b.bottom-
  (v-low)/(yTop-low)*(b.bottom-b.top);

 geometry={b,sx,sy,minX,maxX,low,yTop,horizontal:false};

 ctx.strokeStyle="#334155";
 ctx.fillStyle="#cbd5e1";
 ctx.lineWidth=1;

 for(let i=0;i<=4;i++){
  const v=low+(yTop-low)*i/4;
  const y=sy(v);
  ctx.beginPath();
  ctx.moveTo(b.left,y);
  ctx.lineTo(b.right,y);
  ctx.stroke();
  ctx.textAlign="right";
  ctx.fillText(fmt(v),b.left-9,y);
 }

 for(let i=0;i<=4;i++){
  const t=minX+(maxX-minX)*i/4;
  ctx.textAlign="center";
  ctx.fillText(
   shortTime(t),
   sx(t),
   b.bottom+20
  );
 }

 DATA.series.forEach((s,i)=>{
  const c=colors[i%colors.length];
  ctx.strokeStyle=c;
  ctx.fillStyle=c;
  ctx.lineWidth=2.2;

  if(style==="bars"){
   const base=sy(0);
   const barW=Math.max(
    1.5,
    Math.min(18,(b.right-b.left)/
     Math.max(1,s.points.length*DATA.series.length)*0.75)
   );
   s.points.forEach(p=>{
    const x=sx(p[0])+
     (i-(DATA.series.length-1)/2)*barW;
    const y=sy(p[1]);
    ctx.globalAlpha=.85;
    ctx.fillRect(x-barW/2,Math.min(y,base),barW,Math.max(1,Math.abs(base-y)));
   });
   ctx.globalAlpha=1;
  }else{
   if(style==="area"){
    ctx.beginPath();
    s.points.forEach((p,j)=>{
     if(j===0)ctx.moveTo(sx(p[0]),sy(0));
     ctx.lineTo(sx(p[0]),sy(p[1]));
    });
    const last=s.points[s.points.length-1];
    ctx.lineTo(sx(last[0]),sy(0));
    ctx.closePath();
    ctx.globalAlpha=.18;
    ctx.fill();
    ctx.globalAlpha=1;
   }

   if(style!=="points"){
    ctx.beginPath();
    s.points.forEach((p,j)=>{
     if(j===0)ctx.moveTo(sx(p[0]),sy(p[1]));
     else ctx.lineTo(sx(p[0]),sy(p[1]));
    });
    ctx.stroke();
   }

   if(style==="points"){
    s.points.forEach(p=>{
     ctx.beginPath();
     ctx.arc(sx(p[0]),sy(p[1]),3,0,Math.PI*2);
     ctx.fill();
    });
   }
  }
 });

 if(hover&&hover.timestamp!=null){
  const x=sx(hover.timestamp);
  ctx.strokeStyle="#e2e8f0";
  ctx.setLineDash([4,4]);
  ctx.beginPath();
  ctx.moveTo(x,b.top);
  ctx.lineTo(x,b.bottom);
  ctx.stroke();
  ctx.setLineDash([]);
 }

 reportHeight();
}

function drawHorizontal(w,h){
 const rows=DATA.series.map((s,i)=>({
  name:s.name,
  value:s.current,
  color:colors[i%colors.length]
 })).sort((a,b)=>b.value-a.value);

 const max=Math.max(
  0.000001,
  ...rows.map(r=>Math.abs(r.value))
 );

 const left=Math.min(225,w*0.42);
 const right=w-24;
 const top=18;
 const rowH=32;

 plot.style.height=Math.max(
  270,rows.length*rowH+65
 )+"px";

 const adjusted=resizeCanvas();
 const W=adjusted.w;

 const usable=W-left-26;

 ctx.clearRect(0,0,adjusted.w,adjusted.h);
 ctx.font="12px system-ui";
 ctx.textBaseline="middle";

 geometry={
  horizontal:true,
  rows,
  left,top,rowH
 };

 rows.forEach((r,i)=>{
  const y=top+i*rowH;
  ctx.fillStyle="#cbd5e1";
  ctx.textAlign="right";
  let label=r.name;
  if(label.length>29)label=label.slice(0,26)+"...";
  ctx.fillText(label,left-12,y+11);

  ctx.fillStyle=r.color;
  ctx.fillRect(
   left,y+2,
   Math.max(1,Math.abs(r.value)/max*usable),
   19
  );

  ctx.fillStyle="#e2e8f0";
  ctx.textAlign="left";
  const txt=fmt(r.value);
  ctx.fillText(
   txt,
   Math.min(
    W-60,
    left+Math.abs(r.value)/max*usable+5
   ),
   y+11
  );
 });
}

function findNearest(t,series){
 const p=series.points;
 if(!p.length)return null;
 let best=p[0];
 let dist=Math.abs(best[0]-t);
 for(let i=1;i<p.length;i++){
  const delta=Math.abs(p[i][0]-t);
  if(delta<dist){
   best=p[i];
   dist=delta;
  }
 }
 return best;
}

function moveTooltip(event){
 if(!geometry)return;

 const rect=canvas.getBoundingClientRect();
 const x=event.clientX-rect.left;
 const y=event.clientY-rect.top;
 let content="";

 if(geometry.horizontal){
  const idx=Math.floor(
   (y-geometry.top)/geometry.rowH
  );
  const row=geometry.rows[idx];

  if(!row){
   tooltip.style.display="none";
   return;
  }

  content=
   '<div class="dt">'+escapeHtml(row.name)+'</div>'+
   '<div>'+fmt(row.value)+" "+escapeHtml(DATA.unit)+'</div>'+
   '<div style="color:#94a3b8;margin-top:5px">'+
   '最新有效樣本</div>';

 }else{
  const g=geometry;
  if(x<g.b.left||x>g.b.right||
     y<g.b.top||y>g.b.bottom){
   tooltip.style.display="none";
   hover=null;
   draw();
   return;
  }

  const ts=g.minX+
   (x-g.b.left)/(g.b.right-g.b.left)*
   (g.maxX-g.minX);

  let nearest=null;
  let best=Infinity;

  DATA.series.forEach(s=>{
   const p=findNearest(ts,s);
   if(!p)return;
   const distance=Math.abs(p[0]-ts);
   if(distance<best){
    best=distance;
    nearest=p;
   }
  });

  if(!nearest)return;

  const exact=nearest[0];
  hover={timestamp:exact};

  content=
   '<div class="dt">'+escapeHtml(fullTime(exact))+
   " (UTC+8)</div>";

  DATA.series.forEach((s,i)=>{
   const p=findNearest(exact,s);
   if(!p)return;

   // Do not display a distant sample
   // as if it were at the hovered timestamp.
   const all=s.points;
   let cadence=Infinity;
   for(let k=1;k<all.length;k++){
    cadence=Math.min(
     cadence,
     all[k][0]-all[k-1][0]
    );
   }

   const tolerance=Number.isFinite(cadence)?
    Math.max(cadence*.75,1):1;

   if(Math.abs(p[0]-exact)>tolerance)return;

   content+=
    '<div class="item"><span>'+
    '<span class="swatch" style="background:'+
    colors[i%colors.length]+'"></span>'+
    escapeHtml(s.name)+
    '</span><b>'+
    fmt(p[1])+" "+escapeHtml(DATA.unit)+
    '</b></div>';
  });

  draw();
 }

 tooltip.innerHTML=content;
 tooltip.style.display="block";

 const tipW=Math.min(280,tooltip.offsetWidth||240);
 const tipH=tooltip.offsetHeight||90;

 tooltip.style.left=Math.max(
  3,Math.min(x+15,rect.width-tipW-5)
 )+"px";

 tooltip.style.top=Math.max(
  3,Math.min(y+12,rect.height-tipH-5)
 )+"px";
}

canvas.addEventListener("pointermove",moveTooltip);

canvas.addEventListener("pointerleave",()=>{
 hover=null;
 tooltip.style.display="none";
 draw();
});

controls.querySelectorAll("button").forEach(btn=>{
 btn.addEventListener("click",()=>{
  style=btn.dataset.style;
  plot.style.height=style==="horizontal"?
   Math.max(270,DATA.series.length*32+65)+"px":
   "330px";

  controls.querySelectorAll("button").forEach(b=>{
   b.classList.toggle(
    "active",
    b.dataset.style===style
   );
  });

  tooltip.style.display="none";
  hover=null;
  draw();
 });
});

const active=controls.querySelector(
 '[data-style="'+style+'"]'
);
if(active)active.classList.add("active");

if(style==="horizontal"){
 plot.style.height=Math.max(
  270,DATA.series.length*32+65
 )+"px";
}

window.addEventListener("resize",draw);
window.addEventListener("load",()=>{
 draw();
 reportHeight();
});
if(typeof ResizeObserver!=="undefined"){
 new ResizeObserver(()=>{
  draw();
  reportHeight();
 }).observe(document.body);
}
draw();
reportHeight();

})();
</script>
</body>
</html>"""

        return template.replace("__PAYLOAD__", data_json)

    # =========================================================
    # Grafana PNG rendering
    # =========================================================

    @staticmethod
    def safe_grafana_time(value, default):

        value = str(value or default)

        if re.fullmatch(
            r"(?:now(?:-[0-9]{1,4}[smhdw])?|[0-9]{13})",
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
            raise ValueError("Invalid dashboard UID")

        if tool == "grafana_render_panel":
            panel_id = int(params.get("panel_id") or 0)

            if not 1 <= panel_id <= 100000:
                raise ValueError("Invalid panel ID")

            path = f"render/d-solo/{quote(uid, safe='')}/_"
            title = f"Grafana Panel {panel_id}"

        elif tool == "grafana_render_dashboard":
            panel_id = None
            path = f"render/d/{quote(uid, safe='')}/_"
            title = "Grafana Dashboard"

        else:
            raise ValueError("Unsupported Grafana tool")

        query = {
            "orgId": self.valves.GRAFANA_ORG_ID,
            "from": self.safe_grafana_time(
                params.get("from_time"), "now-1h"
            ),
            "to": self.safe_grafana_time(
                params.get("to_time"), "now"
            ),
            "width": min(
                max(int(params.get("width") or 1000),400),
                1400,
            ),
            "height": min(
                max(int(params.get("height") or 500),250),
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

        if re.fullmatch(r"[A-Za-z0-9_+./-]{1,64}",tz):
            query["tz"] = tz

        variables = params.get("variables")

        if isinstance(variables,str):
            for pair in variables.split(";")[:12]:
                if "=" not in pair:
                    continue

                key,value = [
                    x.strip()
                    for x in pair.split("=",1)
                ]

                if (
                    re.fullmatch(
                        r"var-[A-Za-z0-9_-]{1,64}",key
                    )
                    and re.fullmatch(
                        r"[A-Za-z0-9_.:/ -]{1,128}",value
                    )
                ):
                    query[key] = value

        return title,path,query

    @staticmethod
    def grafana_embed(title,image64):

        safe_title = html.escape(title,quote=True)

        return (
            '<!doctype html><html><head>'
            '<meta charset="utf-8">'
            '<meta name="viewport" '
            'content="width=device-width,initial-scale=1">'
            '<style>'
            'html,body{margin:0;padding:0;'
            'width:100%;overflow:hidden;'
            'background:transparent}'
            'img{display:block;width:100%;'
            'height:auto;max-width:100%}'
            '</style></head><body>'
            f'<img id="grafana" alt="{safe_title}" '
            f'src="data:image/png;base64,{image64}">'
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
    # Open WebUI Pipe
    # =========================================================

    async def pipe(
        self,
        body: dict,
        __user__: dict = None,
        __event_emitter__=None,
        __task__=None,
    ):

        # Keep the existing admin-only security boundary.

        if not __user__ or __user__.get("role") != "admin":
            return "Access denied: administrator access required."

        if __task__:
            return "HolmesGPT SRE"

        messages = body.get("messages") or []

        questions = [
            m.get("content")
            for m in messages
            if (
                m.get("role") == "user"
                and isinstance(m.get("content"),str)
            )
        ]

        if not questions or not questions[-1].strip():
            return "請輸入 Kubernetes 維運問題。"

        question = questions[-1].strip()

        system_prompt = "\n\n".join(
            m.get("content","")
            for m in messages
            if (
                m.get("role") == "system"
                and isinstance(m.get("content"),str)
            )
        )

        headers = {
            "Content-Type":"application/json",
            "Accept":"text/event-stream",
        }

        if self.valves.API_KEY:
            headers["X-API-Key"] = self.valves.API_KEY

        payload = {
            "model":self.valves.MODEL,
            "ask":question,
            "stream":True,
            "enable_tool_approval":False,
            "additional_system_prompt":system_prompt,
        }

        holmes_url = (
            self.valves.HOLMES_URL.rstrip("/")
            + "/api/chat"
        )

        history = []
        params_by_id = {}
        grafana_jobs = []
        prom_jobs = {}

        seen_grafana = set()

        final_answer = None
        pending_event = ""
        pending_lines = []

        async def status(message,done=False):

            if __event_emitter__:
                await __event_emitter__({
                    "type":"status",
                    "data":{
                        "description":message,
                        "done":done,
                        "hidden":False,
                    },
                })

        async def handle_event(event_type,data):

            nonlocal final_answer

            if event_type == "start_tool_calling":

                name = (
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

                if call_id and isinstance(params,dict):
                    params_by_id[call_id] = params

                history.append({
                    "tool":name,
                    "call_id":call_id,
                    "status":"running",
                    "message":"執行中",
                })

                await status(f"正在執行 {name}...")

            elif event_type == "tool_calling_result":

                name = (
                    data.get("name")
                    or data.get("tool_name")
                    or "unknown"
                )

                call_id = (
                    data.get("tool_call_id")
                    or data.get("id")
                )

                result = data.get("result") or {}

                if not isinstance(result,dict):
                    result = {}

                params = (
                    result.get("params")
                    or data.get("params")
                    or params_by_id.get(call_id)
                    or {}
                )

                if not isinstance(params,dict):
                    params = {}

                outcome = str(
                    result.get("status") or "unknown"
                ).lower()

                error = str(
                    result.get("error") or ""
                )

                raw_data = self.parse_object(
                    result.get("data")
                )

                stderr = str(
                    raw_data.get("stderr") or ""
                )

                if (
                    "runtime: out of memory" in stderr.lower()
                    or "fatal error: out of memory" in stderr.lower()
                    or "unable to find resource kind" in stderr.lower()
                ):
                    outcome = "error"
                    error = stderr.splitlines()[0]

                if outcome == "success":
                    message = f"{name} 執行成功"
                else:
                    message = (
                        f"{name} 失敗："
                        f"{(error or stderr or 'unknown')[:160]}"
                    )

                matched = False

                for item in reversed(history):

                    if item["status"] != "running":
                        continue

                    if (
                        (call_id and item["call_id"] == call_id)
                        or item["tool"] == name
                    ):
                        item.update(
                            status=outcome,
                            message=message,
                        )
                        matched=True
                        break

                if not matched:
                    history.append({
                        "tool":name,
                        "call_id":call_id,
                        "status":outcome,
                        "message":message,
                    })

                # Prometheus matrix
                if (
                    name == "execute_prometheus_range_query"
                    and outcome == "success"
                    and call_id
                    and self.valves.SHOW_PROMETHEUS_CHARTS
                ):

                    matrix = self.decode_prometheus_matrix(
                        result
                    )

                    if (
                        matrix is not None
                        and len(prom_jobs) < self.valves.MAX_CHARTS
                    ):
                        prom_jobs[call_id] = {
                            "params":dict(params),
                            "matrix":matrix,
                        }

                # Existing Grafana rendering
                if (
                    self.valves.SHOW_RENDERED_IMAGES
                    and outcome == "success"
                    and name in {
                        "grafana_render_panel",
                        "grafana_render_dashboard",
                    }
                ):

                    key = json.dumps(
                        {"tool":name,"params":params},
                        sort_keys=True,
                        ensure_ascii=False,
                    )

                    if (
                        key not in seen_grafana
                        and len(grafana_jobs)<self.valves.MAX_IMAGES
                    ):
                        seen_grafana.add(key)
                        grafana_jobs.append({
                            "tool":name,
                            "params":dict(params),
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
                    )[:160]
                )

        async def process_sse():

            nonlocal pending_event,pending_lines

            if not pending_lines:
                pending_event=""
                return

            raw = "\n".join(pending_lines)
            event_type = pending_event or "message"

            pending_event=""
            pending_lines=[]

            if raw == "[DONE]":
                return

            try:
                data=json.loads(raw)
            except (ValueError,TypeError):
                return

            if isinstance(data,dict):
                await handle_event(event_type,data)

        try:

            await status("HolmesGPT 正在開始調查")

            timeout=httpx.Timeout(
                float(self.valves.TIMEOUT),
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
                            pending_event=line[6:].strip()

                        elif line.startswith("data:"):
                            pending_lines.append(
                                line[5:].lstrip()
                            )

                    await process_sse()

            if not final_answer:
                await status(
                    "HolmesGPT 沒有完整回答",
                    done=True,
                )
                return "HolmesGPT 未回傳完整調查結果。"

            output=str(final_answer)

            if self.valves.REMOVE_TOOL_IMAGE_REFERENCES:
                output=self.remove_tool_image_refs(output)

            # ---------------------------------------------
            # Remove confirmed PromQL reference markers
            # while preserving a readable chart location.
            # ---------------------------------------------

            used_ids=[]

            marker_pattern=re.compile(
                r"<<\s*(\{.*?\})\s*>>",
                re.DOTALL,
            )

            def replace_marker(match):

                try:
                    marker=json.loads(match.group(1))
                except ValueError:
                    return match.group(0)

                if marker.get("type")!="promql":
                    return match.group(0)

                call_id=marker.get("tool_call_id")

                if call_id in prom_jobs:
                    used_ids.append(call_id)
                    return "（互動式趨勢圖顯示於回答上方）"

                return "（此圖表未取得有效時間序列資料）"

            output=marker_pattern.sub(
                replace_marker,
                output,
            )

            # ---------------------------------------------
            # Emit Prometheus charts
            # ---------------------------------------------

            shown_charts=0
            chart_failures=[]

            if (
                self.valves.SHOW_PROMETHEUS_CHARTS
                and __event_emitter__
            ):

                ordered_ids=list(dict.fromkeys(
                    used_ids+list(prom_jobs.keys())
                ))

                for call_id in ordered_ids[:self.valves.MAX_CHARTS]:

                    try:
                        job=prom_jobs[call_id]

                        chart_data=self.make_chart_data(
                            job,
                            question,
                        )

                        if not chart_data:
                            chart_failures.append(
                                "查詢沒有可繪製的有效資料點"
                            )
                            continue

                        chart=self.chart_html(chart_data)

                        await __event_emitter__({
                            "type":"embeds",
                            "data":{"embeds":[chart]},
                        })

                        shown_charts+=1

                    except (
                        ValueError,
                        TypeError,
                        OverflowError,
                    ) as exc:
                        chart_failures.append(
                            type(exc).__name__
                        )

            # ---------------------------------------------
            # Render Grafana PNG from known service URL
            # ---------------------------------------------

            shown_grafana=0
            grafana_failures=[]

            if (
                grafana_jobs
                and __event_emitter__
                and self.valves.GRAFANA_TOKEN
            ):

                render_headers={
                    "Authorization":
                        "Bearer "+self.valves.GRAFANA_TOKEN,
                    "X-Grafana-Org-Id":
                        str(self.valves.GRAFANA_ORG_ID),
                    "Accept":"image/png",
                }

                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(
                        90.0,
                        connect=10.0,
                    ),
                    follow_redirects=False,
                ) as client:

                    for job in grafana_jobs:

                        try:
                            title,path,query=(
                                self.grafana_render_params(job)
                            )

                            url=(
                                self.valves.GRAFANA_URL.rstrip("/")
                                +"/"+path
                            )

                            response=await client.get(
                                url,
                                params=query,
                                headers=render_headers,
                            )

                            response.raise_for_status()

                            png=response.content

                            if (
                                response.headers.get(
                                    "Content-Type",""
                                ).split(";")[0].strip().lower()
                                != "image/png"
                            ):
                                raise ValueError("Not PNG")

                            if not png.startswith(
                                b"\x89PNG\r\n\x1a\n"
                            ):
                                raise ValueError("Invalid PNG")

                            if len(png)>self.valves.MAX_IMAGE_BYTES:
                                raise ValueError("PNG too large")

                            encoded=base64.b64encode(
                                png
                            ).decode("ascii")

                            embed=self.grafana_embed(
                                title,
                                encoded,
                            )

                            await __event_emitter__({
                                "type":"embeds",
                                "data":{"embeds":[embed]},
                            })

                            shown_grafana+=1

                        except (
                            httpx.HTTPError,
                            ValueError,
                            TypeError,
                            OverflowError,
                        ) as exc:
                            grafana_failures.append(
                                type(exc).__name__
                            )

            # ---------------------------------------------
            # Tool history
            # ---------------------------------------------

            if self.valves.SHOW_TOOL_HISTORY and history:

                lines=[
                    "",
                    "---",
                    "### 工具執行紀錄",
                ]

                for i,item in enumerate(history[:30],1):

                    symbol=(
                        "✓"
                        if item["status"]=="success"
                        else "!"
                    )

                    lines.append(
                        f"{i}. {symbol} "
                        f"`{item['tool']}` — "
                        f"{item['message']}"
                    )

                output+="\n".join(lines)

            if shown_charts:
                output+=(
                    f"\n\n已附上 {shown_charts} 張 "
                    "Prometheus 互動式圖表。"
                )

            if shown_grafana:
                output+=(
                    f"\n\n已附上 {shown_grafana} 張 "
                    "Grafana 圖片。"
                )

            if chart_failures:
                output+=(
                    "\n\n> 部分趨勢圖未能產生："
                    + "、".join(chart_failures)
                )

            if grafana_failures:
                output+=(
                    "\n\n> 部分 Grafana 圖片失敗："
                    + "、".join(grafana_failures)
                )

            await status(
                "Kubernetes 調查完成",
                done=True,
            )

            return output

        except httpx.TimeoutException:

            await status("HolmesGPT 調查逾時",done=True)
            return "HolmesGPT 調查逾時。"

        except httpx.HTTPStatusError as exc:

            await status("HolmesGPT HTTP 錯誤",done=True)
            return (
                "HolmesGPT HTTP 錯誤："
                + str(exc.response.status_code)
            )

        except httpx.RequestError:

            await status("HolmesGPT 連線失敗",done=True)
            return "無法連線 HolmesGPT API。"

        except Exception as exc:

            await status("Pipe 執行失敗",done=True)
            return (
                "Pipe 處理錯誤："
                + type(exc).__name__
            )
