"""Append an evidence-first official Sporttery market snapshot.

This collector keeps a time series even when external Asian/European feeds are
unavailable. Missing markets are recorded explicitly as ``unavailable``.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
URL = (
    "https://webapi.sporttery.cn/gateway/uniform/football/"
    "getMatchCalculatorV1.qry?channel=c&poolCode=ttg,had,hhad,crs,hafu"
)
HEADERS = {
    "User-Agent": "Mozilla/5.0 football-radar-market-snapshot/1.0",
    "Referer": "https://m.sporttery.cn/mjc/jsq/zqzjq/",
    "Origin": "https://m.sporttery.cn",
}


def fetch() -> dict:
    request = Request(URL, headers=HEADERS)
    with urlopen(request, timeout=45) as response:
        return json.loads(response.read())


def pool(raw: dict | None) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    result = {}
    for key, item in raw.items():
        if key.endswith("f") or key in {"updateDate", "updateTime"}:
            continue
        if item in (None, "", []):
            continue
        try:
            result[key] = float(item)
        except (TypeError, ValueError):
            result[key] = str(item)
    return result


def changed_odds(before: dict, after: dict, field: str) -> str:
    """List every changed goal or score odd, preserving the exact values."""
    rows = []
    for key in sorted(set(before) | set(after)):
        try:
            old, new = float(before.get(key)), float(after.get(key))
        except (TypeError, ValueError):
            continue
        if old == new:
            continue
        if field == "goals":
            label = f"{key[1:]}球" if key.startswith("s") else key
        elif field == "exactScores" and key.startswith("s") and len(key) == 6:
            label = f"{key[1:3]}-{key[4:6]}"
        else:
            label = key
        rows.append(f"{label} {old:g}→{new:g}")
    return "、".join(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    args = parser.parse_args()
    target = f"{args.date[:4]}-{args.date[4:6]}-{args.date[6:8]}"
    payload = fetch()
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    hour = datetime.now().astimezone().hour
    period = "凌晨" if hour < 6 else "上午" if hour < 12 else "下午" if hour < 18 else "晚间"
    rows = []
    for group in payload.get("value", {}).get("matchInfoList", []):
        if group.get("businessDate") != target:
            continue
        for match in group.get("subMatchList", []):
            if match.get("businessDate") != target:
                continue
            ttg = pool(match.get("ttg"))
            crs = {key: float(value) for key, value in pool(match.get("crs")).items()
                   if key.startswith("s") and len(key) == 6}
            rows.append({
                "capturedAt": now,
                "businessDate": target,
                "matchNum": match.get("matchNumStr", "unavailable"),
                "matchId": str(match.get("matchId", "unavailable")),
                "kickoff": f"{match.get('matchDate', target)}T{match.get('matchTime', 'unavailable')}+08:00",
                "league": match.get("leagueAllName") or match.get("leagueAbbName") or "unavailable",
                "home": match.get("homeTeamAllName") or match.get("homeTeamAbbName") or "unavailable",
                "away": match.get("awayTeamAllName") or match.get("awayTeamAbbName") or "unavailable",
                "score": "not_started",
                "handicap": pool(match.get("hhad")) or {"status": "unavailable"},
                "european": pool(match.get("had")) or {"status": "unavailable"},
                "goals": ttg or {"status": "unavailable"},
                "exactScores": crs or {"status": "unavailable"},
                "source": URL,
                "status": match.get("matchStatus", "unavailable"),
            })
    if not rows:
        raise SystemExit(f"no official matches for businessDate={target}")
    rows.sort(key=lambda row: row["matchNum"])
    data_path = ROOT / "data" / f"market_snapshots_{args.date}.jsonl"
    data_path.parent.mkdir(parents=True, exist_ok=True)
    with data_path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    all_rows = []
    with data_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                all_rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    all_rows.sort(key=lambda row: (row.get("capturedAt", ""), row.get("matchNum", "")))
    previous = {}
    annotated = []
    for row in all_rows:
        old = previous.get(row.get("matchNum"))
        changed = []
        details = []
        if old:
            for field, label in (("handicap", "亚盘"), ("european", "欧赔"), ("goals", "进球数赔率"), ("exactScores", "比分赔率"), ("score", "比分")):
                before, after = old.get(field), row.get(field)
                if before != after:
                    changed.append(label)
                    if isinstance(before, dict) and isinstance(after, dict):
                        ups = downs = 0
                        for key in set(before) | set(after):
                            try:
                                a, b = float(before.get(key)), float(after.get(key))
                            except (TypeError, ValueError):
                                continue
                            if b > a: ups += 1
                            elif b < a: downs += 1
                        if field in {"goals", "exactScores"}:
                            values = changed_odds(before, after, field)
                            details.append(f"{label}：{values}" if values else f"{label}{ups}项上升/{downs}项下降")
                        else:
                            details.append(f"{label}{ups}项上升/{downs}项下降")
        if not old:
            interpretation = "首个时间点，建立基准"
        elif changed:
            interpretation = "；".join(details or changed) + "。赔率下降表示该档位市场隐含概率相对上升，盘口变化仍不等于结果确定"
        else:
            interpretation = "本时间点未识别到数值变化；不等于外盘无变化"
        annotated.append((row, period, interpretation))
        previous[row.get("matchNum")] = row

    table_path = ROOT / "output" / f"market_tracking_{args.date}.md"
    table_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# 盘口水位追踪（{target}）", "", 
        f"本次抓取：{now}；官方场次数：{len(rows)}；累计时间点记录：{len(annotated)}；来源：中国竞彩网接口。", "",
        "|编号|开赛|主队|客队|时段|时间点|比分|让球线|让球水位|欧赔|总进球数赔率|状态|变动解读|",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    lines.extend(
        f"|{r.get('matchNum', 'unavailable')}|{r.get('kickoff', 'unavailable')[11:16]}|{r.get('home', 'unavailable')}|{r.get('away', 'unavailable')}|{period_label}|{r.get('capturedAt', 'unavailable')}|{r.get('score', 'unavailable')}|{r.get('handicap', {}).get('goalLineValue', 'unavailable')}|{r.get('handicap', 'unavailable')}|{r.get('european', 'unavailable')}|{r.get('goals', 'unavailable')}|{r.get('status', 'unavailable')}|{interpretation}|"
        for r, period_label, interpretation in annotated
    )
    lines += ["", "注：当前官方接口未必提供海外亚盘/欧赔走势；未提供字段统一记为 unavailable，待 500/海外源可读时追加同一时间点的来源数据。", "以上仅为公开信息整理后的娱乐分析，不构成任何购彩建议，请理性参考。"]
    table_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    latest = {}
    for row in all_rows:
        latest[row.get("matchNum")] = row
    stage_rows = [row for row in latest.values() if any(tag in row.get("league", "") for tag in ("日本", "韩国", "J1", "J2", "K1", "K2"))]
    if stage_rows:
        stage_path = ROOT / "output" / f"stage_summary_{args.date}_1700.md"
        stage_lines = [
            f"# 日本/韩国阶段汇总（{target}）", "",
            f"生成时间：{now}；本次官方接口返回：{len(rows)}场；当前日本/韩国相关场次：{len(stage_rows)}场。", "",
            "|编号|联赛|开赛|主队|客队|比分|盘口状态|欧赔状态|进球数状态|阶段说明|",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for row in sorted(stage_rows, key=lambda item: item.get("matchNum", "")):
            history = [x for x in all_rows if x.get("matchNum") == row.get("matchNum")]
            old = history[-2] if len(history) > 1 else None
            changed = []
            if old:
                for field, label in (("handicap", "亚盘"), ("european", "欧赔"), ("goals", "进球数"), ("score", "比分")):
                    if old.get(field) != row.get(field): changed.append(label)
            stage_lines.append(
                f"|{row.get('matchNum','unavailable')}|{row.get('league','unavailable')}|{row.get('kickoff','unavailable')[11:16]}|{row.get('home','unavailable')}|{row.get('away','unavailable')}|{row.get('score','unavailable')}|{row.get('handicap','unavailable')}|{row.get('european','unavailable')}|{row.get('goals','unavailable')}|{('；'.join(changed)+'发生变动，结合前后值解读' if changed else '本时间点未识别到变化；比分/盘口缺失项按 unavailable 处理')}|"
            )
        stage_lines += ["", "说明：001、002若不在本次官方返回列表中，只保留历史快照，不补采新盘口。", "以上仅为公开信息整理，不构成购彩建议。"]
        stage_path.write_text("\n".join(stage_lines) + "\n", encoding="utf-8")
    print(json.dumps({"captured": len(rows), "data": str(data_path), "table": str(table_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
