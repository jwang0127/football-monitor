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


def value(raw: dict, *keys: str) -> str:
    for key in keys:
        item = raw.get(key)
        if item not in (None, "", []):
            return str(item)
    return "unavailable"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    args = parser.parse_args()
    target = f"{args.date[:4]}-{args.date[4:6]}-{args.date[6:8]}"
    payload = fetch()
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    rows = []
    for group in payload.get("value", {}).get("matchInfoList", []):
        if group.get("businessDate") != target:
            continue
        for match in group.get("subMatchList", []):
            if match.get("businessDate") != target:
                continue
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
                "asianHandicap": value(match, "hhad", "had"),
                "europeanOdds": value(match, "had"),
                "goalLineOdds": value(match, "ttg"),
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
    table_path = ROOT / "output" / f"market_tracking_{args.date}.md"
    table_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# 盘口水位追踪（{target}）", "", 
        f"本次抓取：{now}；官方场次数：{len(rows)}；来源：中国竞彩网接口。", "",
        "|编号|开赛|主队|客队|时间点|比分|亚盘/水位|欧赔|进球数赔率|状态|",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    lines.extend(
        f"|{r['matchNum']}|{r['kickoff'][11:16]}|{r['home']}|{r['away']}|{r['capturedAt']}|{r['score']}|{r['asianHandicap']}|{r['europeanOdds']}|{r['goalLineOdds']}|{r['status']}|"
        for r in rows
    )
    lines += ["", "注：当前官方接口未必提供海外亚盘/欧赔走势；未提供字段统一记为 unavailable，待 500/海外源可读时追加同一时间点的来源数据。", "以上仅为公开信息整理后的娱乐分析，不构成任何购彩建议，请理性参考。"]
    table_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"captured": len(rows), "data": str(data_path), "table": str(table_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
