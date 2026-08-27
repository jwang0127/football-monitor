"""Build the public page for one Sporttery business date only."""
from __future__ import annotations

import argparse
import html
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def category(league: str) -> str:
    return "杯赛" if "杯" in league or "Cup" in league else "联赛"


def build(date: str) -> Path:
    sporttery = json.loads((ROOT / "data" / f"sporttery_{date}.json").read_text(encoding="utf-8"))
    snapshot_path = ROOT / "data" / f"market_snapshots_{date}.jsonl"
    latest = {}
    if snapshot_path.exists():
        for line in snapshot_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                latest[row["matchNum"]] = row

    groups = defaultdict(list)
    for match in sporttery["matches"]:
        groups[category(match["league"])].append(match)
    sections = []
    for group_name in ("联赛", "杯赛"):
        cards = []
        for match in groups.get(group_name, []):
            snap = latest.get(match["matchNumStr"], {})
            had = match["pools"].get("had", {}).get("values", {})
            hhad = match["pools"].get("hhad", {}).get("values", {})
            ttg = match["pools"].get("ttg", {}).get("values", {})
            search = " ".join((match["matchNumStr"], match["league"], match["home"], match["away"]))
            cards.append(
                f'<article class="card" data-search="{html.escape(search.lower())}">'
                f'<div class="top"><strong>{html.escape(match["matchNumStr"])}</strong>'
                f'<span>{html.escape(match["league"])}</span></div>'
                f'<h3>{html.escape(match["home"])} <small>vs</small> {html.escape(match["away"])}</h3>'
                f'<p class="meta">开赛：{html.escape(match["kickoff"])} · 状态：{html.escape(match["matchStatus"])}</p>'
                f'<div class="markets"><b>胜平负</b> 主 {had.get("h", "unavailable")} / 平 {had.get("d", "unavailable")} / 客 {had.get("a", "unavailable")}</div>'
                f'<div class="markets"><b>让球</b> {hhad.get("goalLineValue", "unavailable")} · 主 {hhad.get("h", "unavailable")} / 客 {hhad.get("a", "unavailable")}</div>'
                f'<div class="markets"><b>进球数</b> 0球 {ttg.get("s0", "unavailable")} · 1球 {ttg.get("s1", "unavailable")} · 2球 {ttg.get("s2", "unavailable")} · 3球 {ttg.get("s3", "unavailable")}</div>'
                f'<div class="markets"><b>比分</b> 当前比分：{html.escape(str(snap.get("score", "unavailable")))}</div>'
                '</article>'
            )
        if cards:
            sections.append(f'<section><h2>{group_name} <em>{len(cards)}场</em></h2><div class="grid">{"".join(cards)}</div></section>')

    page = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>足球雷达 {date}</title>
<style>:root{{--ink:#14201c;--paper:#f5f0e6;--green:#0f5b46;--lime:#c8ef65;--line:#cabfae}}*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.6 system-ui,"Microsoft YaHei",sans-serif}}header,main,footer{{max-width:1180px;margin:auto;padding:24px}}header{{padding-top:48px}}h1{{font:800 clamp(40px,7vw,76px)/.95 Georgia,serif;margin:8px 0}}.eyebrow{{letter-spacing:.16em;color:var(--green);font-weight:800}}.search{{width:100%;padding:14px 16px;border:1px solid var(--ink);background:#fff;font-size:16px;margin:22px 0}}section{{margin:34px 0}}h2{{border-bottom:2px solid var(--ink);padding-bottom:8px}}h2 em{{font-size:14px;font-style:normal;color:#6a6258}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:16px}}.card{{background:#fff;border:1px solid var(--line);padding:18px;box-shadow:6px 6px 0 #dcd2c2}}.top{{display:flex;justify-content:space-between;gap:10px;color:var(--green)}}h3{{margin:12px 0 4px;font-size:21px}}h3 small{{font-size:12px;color:#786f63}}.meta{{color:#6a6258;font-size:13px}}.markets{{padding:7px 0;border-top:1px solid #eee}}.markets b{{display:inline-block;width:65px;color:var(--green)}}footer{{border-top:1px solid var(--line);margin-top:20px;color:#6a6258}}</style></head>
<body><header><p class="eyebrow">SPORTTERY · TODAY ONLY</p><h1>足球雷达</h1><p>{date} 竞彩业务日 · {len(sporttery["matches"])} 场官方赛程</p><input id="search" class="search" placeholder="搜索编号、联赛、主队或客队…" aria-label="搜索比赛"></header><main>{''.join(sections)}</main><footer><p>盘口与赔率为当前采集时点；unavailable 表示来源未提供，不代表没有数据。以上仅为公开信息整理后的娱乐分析，不构成任何购彩建议，请理性参考。</p></footer>
<script>const input=document.querySelector('#search');input.addEventListener('input',()=>{{const q=input.value.trim().toLowerCase();document.querySelectorAll('.card').forEach(c=>c.hidden=q&&!c.dataset.search.includes(q));document.querySelectorAll('section').forEach(s=>s.hidden=!s.querySelector('.card:not([hidden])'));}});</script></body></html>'''
    out = ROOT / date / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    (ROOT / "index.html").write_text(page, encoding="utf-8")
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="20260823")
    args = parser.parse_args()
    print(build(args.date))
