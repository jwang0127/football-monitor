"""Render every match as a home/away, sub-item-by-sub-item framework audit."""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

import yuanbao_python_20260820_ZhZRAn as radar


ROOT = Path(__file__).resolve().parent
LABEL = {"home": "主胜", "draw": "平", "away": "客胜"}
HAFU_LABEL = {
    "hh": "胜胜", "hd": "胜平", "ha": "胜负",
    "dh": "平胜", "dd": "平平", "da": "平负",
    "ah": "负胜", "ad": "负平", "aa": "负负",
}


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sample_level(count: int) -> str:
    if count >= 5:
        return "充足"
    if count >= 3:
        return "中等"
    return "低样本，模型已向市场基准收缩"


def result_for_team(row: dict[str, Any], team_id: int) -> str:
    home_goals, away_goals = (int(x) for x in row["score"].split("-"))
    is_home = int(row["homeId"]) == team_id
    goals_for, goals_against = (home_goals, away_goals) if is_home else (away_goals, home_goals)
    result = "胜" if goals_for > goals_against else "平" if goals_for == goals_against else "负"
    opponent = row["away"] if is_home else row["home"]
    return f"{row['date'][:10]} 对 {opponent}，{goals_for}-{goals_against}（{result}）"


def h2h_summary(match: dict[str, Any]) -> dict[str, Any]:
    ext = match["external"]
    home_id = int(ext["fixture"]["homeId"])
    wins = draws = losses = gf = ga = 0
    rows = ext["headToHead"]["records"]
    for row in rows:
        hg, ag = (int(x) for x in row["score"].split("-"))
        is_home = int(row["homeId"]) == home_id
        team_gf, team_ga = (hg, ag) if is_home else (ag, hg)
        gf += team_gf; ga += team_ga
        if team_gf > team_ga: wins += 1
        elif team_gf == team_ga: draws += 1
        else: losses += 1
    return {
        "count": len(rows), "homePerspective": f"{wins}胜{draws}平{losses}负，进{gf}失{ga}",
        "latest": (f"{rows[0]['date'][:10]} {rows[0]['home']} {rows[0]['score']} {rows[0]['away']}"
                   if rows else "接口已核验，历史交锋记录数为0"),
        "source": ext["headToHead"]["source"],
    }


def external_consensus(raw_record: dict[str, Any]) -> dict[str, Any]:
    samples = {"home": [], "draw": [], "away": []}
    books = []
    for bookmaker in raw_record.get("bookmakers") or []:
        bet = next((row for row in bookmaker.get("bets") or [] if row.get("name") == "Match Winner"), None)
        if not bet:
            continue
        values = {str(row.get("value")): row.get("odd") for row in bet.get("values") or []}
        try:
            odds = {"home": float(values["Home"]), "draw": float(values["Draw"]), "away": float(values["Away"])}
        except (KeyError, TypeError, ValueError):
            continue
        books.append(bookmaker.get("name") or f"bookmaker-{bookmaker.get('id')}")
        for key in samples:
            samples[key].append(odds[key])
    if not books:
        return {"status": "checked_without_usable_1x2", "bookmakerCount": 0,
                "medianOdds": "不参与模型", "favorite": "不参与模型"}
    medians = {key: round(statistics.median(values), 3) for key, values in samples.items()}
    favorite = min(medians, key=medians.get)
    return {"status": "checked", "bookmakerCount": len(books), "medianOdds": medians,
            "favorite": LABEL[favorite], "sampleBookmakers": books[:5]}


def top_ttg(pool: dict[str, Any], limit: int = 3) -> list[dict[str, Any]]:
    values = pool["values"]
    rows = [{"goals": "7+" if key == "s7" else key[1:], "odds": float(value)}
            for key, value in values.items() if key.startswith("s") and key[1:].isdigit()]
    return sorted(rows, key=lambda row: row["odds"])[:limit]


def crs_label(key: str) -> str:
    if key == "s1sh": return "胜其他"
    if key == "s1sd": return "平其他"
    if key == "s1sa": return "负其他"
    return f"{int(key[1:3])}-{int(key[4:6])}"


def top_crs(pool: dict[str, Any], limit: int = 6) -> list[dict[str, Any]]:
    rows = [{"score": crs_label(key), "odds": float(value)} for key, value in pool["values"].items()]
    return sorted(rows, key=lambda row: row["odds"])[:limit]


def top_hafu(pool: dict[str, Any], limit: int = 3) -> list[dict[str, Any]]:
    rows = [{"path": HAFU_LABEL[key], "odds": float(value)} for key, value in pool["values"].items()
            if key in HAFU_LABEL]
    return sorted(rows, key=lambda row: row["odds"])[:limit]


def side_motivation(mode: str, side: str) -> str:
    if mode == "two_leg_first":
        return "主场首回合需建立领先，同时控制被反击" if side == "home" else "客场首回合优先控制失球，为次回合保留空间"
    if mode == "two_leg_second":
        return "次回合主场需要结合首回合比分管理晋级路径" if side == "home" else "次回合客队需在防守与追分之间动态切换"
    if mode == "cup_single":
        return "单场淘汰，落后后的风险偏好会快速上升"
    return "联赛积分驱动；赛季早段样本不足时不夸大排名含义"


def build_match(match: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    p, ext = match["prediction"], match["external"]
    home_form, away_form = p["evidence"]["homeForm"], p["evidence"]["awayForm"]
    home_id, away_id = int(ext["fixture"]["homeId"]), int(ext["fixture"]["awayId"])
    injuries = ext["injuries"]["records"]
    home_injuries = [row for row in injuries if row["team"] == ext["fixture"]["home"]]
    away_injuries = [row for row in injuries if row["team"] == ext["fixture"]["away"]]
    h2h = h2h_summary(match)
    pools = match["officialPools"]
    market = p["marketProbabilities"]
    raw_external = evidence["matches"][match["matchId"]]["externalOdds"]["records"]
    consensus = external_consensus(raw_external[0] if raw_external else {})
    had_text = (pools["had"]["values"] if pools["had"]["status"] == "offered" else
                {"method": "由官方CRS比分赔率按胜平负边际化", "status": pools["had"]["status"]})
    hhad = pools["hhad"]["values"]
    attack_home = round(home_form["goalsFor"] / home_form["matches"], 2)
    attack_away = round(away_form["goalsFor"] / away_form["matches"], 2)
    concede_home = round(home_form["goalsAgainst"] / home_form["matches"], 2)
    concede_away = round(away_form["goalsAgainst"] / away_form["matches"], 2)
    direction_probability = p["finalProbabilities"][p["direction"]]
    framework_home = round(p["layers"]["hardPower"]["home"] * .40 +
                           p["layers"]["tacticalMatchup"]["home"] * .35 +
                           p["layers"]["psychologyAndSchedule"]["home"] * .25, 2)
    framework_away = round(p["layers"]["hardPower"]["away"] * .40 +
                           p["layers"]["tacticalMatchup"]["away"] * .35 +
                           p["layers"]["psychologyAndSchedule"]["away"] * .25, 2)
    if p["competitionMode"].startswith("two_leg"):
        if p["direction"] == "home":
            advancement = f"{match['home']}（两回合倾向，次回合前必须重算）"
        elif p["direction"] == "away":
            advancement = f"{match['away']}（两回合倾向，次回合前必须重算）"
        else:
            advancement = f"{match['home'] if framework_home >= framework_away else match['away']}（90分钟偏平下的两回合倾向）"
    else:
        advancement = "联赛赛制，不适用晋级字段"
    return {
        "matchId": match["matchId"], "matchNumStr": match["matchNumStr"],
        "fixture": {"home": match["home"], "away": match["away"], "kickoffBeijing": match["kickoff"],
                    "competition": ext["fixture"]["competition"], "round": ext["fixture"]["round"],
                    "venue": ext["fixture"]["venue"], "referee": ext["fixture"]["referee"]},
        "hardPower": {
            "weight": p["layers"]["hardPower"]["weight"],
            "home": {"team": match["home"], "sample": home_form, "sampleAssessment": sample_level(home_form["matches"]),
                     "lastMatch": result_for_team(ext["recent"]["home"][0], home_id),
                     "modelScore": p["layers"]["hardPower"]["home"]},
            "away": {"team": match["away"], "sample": away_form, "sampleAssessment": sample_level(away_form["matches"]),
                     "lastMatch": result_for_team(ext["recent"]["away"][0], away_id),
                     "modelScore": p["layers"]["hardPower"]["away"]},
            "judgement": (f"硬实力层倾向{'主队' if p['layers']['hardPower']['home'] > p['layers']['hardPower']['away'] else '客队'}；"
                          "低于5场的样本已做收缩，不直接外推。")},
        "tacticalMatchup": {
            "weight": p["layers"]["tacticalMatchup"]["weight"],
            "home": {"team": match["home"], "attackGoalsPerMatch": attack_home,
                     "concededPerMatch": concede_home, "opponentConcededPerMatch": concede_away,
                     "confirmedInjuryRecords": home_injuries,
                     "injuryAudit": f"伤停端点核验，去重后{len(home_injuries)}条",
                     "modelScore": p["layers"]["tacticalMatchup"]["home"]},
            "away": {"team": match["away"], "attackGoalsPerMatch": attack_away,
                     "concededPerMatch": concede_away, "opponentConcededPerMatch": concede_home,
                     "confirmedInjuryRecords": away_injuries,
                     "injuryAudit": f"伤停端点核验，去重后{len(away_injuries)}条",
                     "modelScore": p["layers"]["tacticalMatchup"]["away"]},
            "headToHead": h2h,
            "goalsMarketTop3": top_ttg(pools["ttg"]),
            "judgement": (f"主队进攻{attack_home}/场对客队失球{concede_away}/场；"
                          f"客队进攻{attack_away}/场对主队失球{concede_home}/场。")},
        "psychologySchedule": {
            "weight": p["layers"]["psychologyAndSchedule"]["weight"],
            "home": {"team": match["home"], "formTrail": home_form["recentTrailNewestFirst"],
                     "restDays": p["evidence"]["homeRestDays"], "motivation": side_motivation(p["competitionMode"], "home"),
                     "modelScore": p["layers"]["psychologyAndSchedule"]["home"]},
            "away": {"team": match["away"], "formTrail": away_form["recentTrailNewestFirst"],
                     "restDays": p["evidence"]["awayRestDays"], "motivation": side_motivation(p["competitionMode"], "away"),
                     "modelScore": p["layers"]["psychologyAndSchedule"]["away"]},
            "weather": ext["weather"],
            "judgement": f"休息天数主{p['evidence']['homeRestDays']}天、客{p['evidence']['awayRestDays']}天；天气只作节奏修正，不替代实力判断。",
        },
        "tournamentCorrection": {"mode": p["competitionMode"], "round": ext["fixture"]["round"],
                                 "applied": p["corrections"]},
        "oddsAudit": {
            "sportterySnapshot": pools["ttg"]["updatedAt"], "had": had_text,
            "marketProbabilities": market,
            "hhad": {"line": hhad.get("goalLine", hhad.get("goalLineValue")),
                     "home": hhad["h"], "draw": hhad["d"], "away": hhad["a"]},
            "totalGoalsTop3": top_ttg(pools["ttg"]), "scoreTop6": top_crs(pools["crs"]),
            "halfFullTop3": top_hafu(pools["hafu"]), "externalConsensus": consensus,
            "movementStatus": "当前单时点快照；未用单点数据伪装赔率走势",
        },
        "overheatAudit": {"detected": p["overheatDetected"],
                          "marketFavorite": LABEL[max(market, key=market.get)],
                          "marketFavoriteProbability": round(max(market.values()), 4),
                          "action": next((x for x in p["corrections"] if "热门" in x), "未触发热门降温")},
        "final": {"result90Minutes": LABEL[p["direction"]], "protection": LABEL[p["protection"]],
                  "probabilities": p["finalProbabilities"], "directionProbability": direction_probability,
                  "frameworkTotal": {"home": framework_home, "away": framework_away},
                  "advancementLean": advancement,
                  "totalGoals": p["totalGoalsMode"], "mainScore": p["mainScore"],
                  "safeScores": p["safeScores"], "tailScore": p["tailScore"], "upsetScore": p["upsetScore"],
                  "confidence": p["confidence"],
                  "riskTrigger": "若弱势方先入球，重新评估双方得分与3+球路径；首回合不可把晋级倾向等同90分钟胜负。"},
        "sources": [radar.SPORTTERY_URL, ext["recent"]["homeSource"], ext["recent"]["awaySource"],
                    ext["injuries"]["source"], ext["headToHead"]["source"], ext["externalOdds"]["source"],
                    ext["competitionAuthority"], ext["weather"]["source"]],
        "completeness": match["completeness"],
    }


def fmt_rows(rows: list[dict[str, Any]], left: str, right: str) -> str:
    return "；".join(f"{row[left]}@{row[right]}" for row in rows)


def injury_text(side: dict[str, Any]) -> str:
    rows = side["confirmedInjuryRecords"]
    if not rows:
        return side["injuryAudit"]
    return "；".join(f"{row['player']}（{row['type']}，{row['reason']}）" for row in rows)


def render(payload: dict[str, Any]) -> str:
    lines = [f"# 逐场三维框架完整分析 · {payload['businessDate']}", "",
             f"生成：{payload['generatedAt']}；官方竞彩全局快照：{payload['sportteryLastUpdateTime']}。", "",
             "每场固定顺序：赛程 → 硬实力 → 战术克制 → 心理/赛程 → 赛制修正 → 五类赔率 → 过热 → 最终结论。", ""]
    for row in payload["matches"]:
        f, hard, tactic, psych, odds, final = row["fixture"], row["hardPower"], row["tacticalMatchup"], row["psychologySchedule"], row["oddsAudit"], row["final"]
        had = odds["had"]
        had_text = (f"主{had['h']} / 平{had['d']} / 客{had['a']}" if "h" in had else had["method"])
        consensus = odds["externalConsensus"]
        consensus_text = (f"{consensus['bookmakerCount']}家公司中位数 主{consensus['medianOdds']['home']} / 平{consensus['medianOdds']['draw']} / 客{consensus['medianOdds']['away']}"
                          if consensus["status"] == "checked" else consensus["status"])
        lines += [f"## {row['matchNumStr']} {f['home']} vs {f['away']}", "",
                  f"**赛程事实**：{f['kickoffBeijing']}，{f['competition']} / {f['round']}，场地 {f['venue']}，裁判 {f['referee']}。", "",
                  f"### 1. 硬实力（权重 {hard['weight']:.0%}）", "",
                  f"- 主队 {hard['home']['team']}：近{hard['home']['sample']['matches']}场 {hard['home']['sample']['wins']}胜{hard['home']['sample']['draws']}平{hard['home']['sample']['losses']}负，进{hard['home']['sample']['goalsFor']}失{hard['home']['sample']['goalsAgainst']}，场均积分{hard['home']['sample']['pointsPerGame']}，样本{hard['home']['sampleAssessment']}；最近一场：{hard['home']['lastMatch']}；评分 {hard['home']['modelScore']}。",
                  f"- 客队 {hard['away']['team']}：近{hard['away']['sample']['matches']}场 {hard['away']['sample']['wins']}胜{hard['away']['sample']['draws']}平{hard['away']['sample']['losses']}负，进{hard['away']['sample']['goalsFor']}失{hard['away']['sample']['goalsAgainst']}，场均积分{hard['away']['sample']['pointsPerGame']}，样本{hard['away']['sampleAssessment']}；最近一场：{hard['away']['lastMatch']}；评分 {hard['away']['modelScore']}。",
                  f"- 小结：{hard['judgement']}", "",
                  f"### 2. 战术克制（权重 {tactic['weight']:.0%}）", "",
                  f"- 主队：场均进球 {tactic['home']['attackGoalsPerMatch']}，场均失球 {tactic['home']['concededPerMatch']}，对手场均失球 {tactic['home']['opponentConcededPerMatch']}；伤停：{injury_text(tactic['home'])}；评分 {tactic['home']['modelScore']}。",
                  f"- 客队：场均进球 {tactic['away']['attackGoalsPerMatch']}，场均失球 {tactic['away']['concededPerMatch']}，对手场均失球 {tactic['away']['opponentConcededPerMatch']}；伤停：{injury_text(tactic['away'])}；评分 {tactic['away']['modelScore']}。",
                  f"- 交锋：{tactic['headToHead']['count']}场，主队视角 {tactic['headToHead']['homePerspective']}；最近一次 {tactic['headToHead']['latest']}。",
                  f"- 总进球市场前三：{fmt_rows(tactic['goalsMarketTop3'], 'goals', 'odds')}。",
                  f"- 小结：{tactic['judgement']}", "",
                  f"### 3. 心理与赛程（权重 {psych['weight']:.0%}）", "",
                  f"- 主队：走势 {psych['home']['formTrail']}，休息 {psych['home']['restDays']} 天；动机：{psych['home']['motivation']}；评分 {psych['home']['modelScore']}。",
                  f"- 客队：走势 {psych['away']['formTrail']}，休息 {psych['away']['restDays']} 天；动机：{psych['away']['motivation']}；评分 {psych['away']['modelScore']}。",
                  f"- 环境：{psych['weather']['location']}，约 {psych['weather']['temperatureC']}°C，降水 {psych['weather']['precipitationProbabilityPct']}%，风速 {psych['weather']['windSpeedKmh']}km/h。",
                  f"- 小结：{psych['judgement']}", "",
                  "### 4. 赛制修正", "",
                  f"- 模式：{row['tournamentCorrection']['mode']}；修正：{'；'.join(row['tournamentCorrection']['applied'])}。", "",
                  "### 5. 五类赔率与外部盘口校验", "",
                  f"- 胜平负 HAD：{had_text}；隐含概率 主{odds['marketProbabilities']['home']:.1%} / 平{odds['marketProbabilities']['draw']:.1%} / 客{odds['marketProbabilities']['away']:.1%}。",
                  f"- 让球胜平负 HHAD：让球 {odds['hhad']['line']}，主{odds['hhad']['home']} / 平{odds['hhad']['draw']} / 客{odds['hhad']['away']}。",
                  f"- 总进球 TTG：{fmt_rows(odds['totalGoalsTop3'], 'goals', 'odds')}。",
                  f"- 比分 CRS 前六：{fmt_rows(odds['scoreTop6'], 'score', 'odds')}。",
                  f"- 半全场 HAFU 前三：{fmt_rows(odds['halfFullTop3'], 'path', 'odds')}。",
                  f"- 外部公司：{consensus_text}；{odds['movementStatus']}。", "",
                  "### 6. 过热检测", "",
                  f"- 市场热门：{row['overheatAudit']['marketFavorite']}（{row['overheatAudit']['marketFavoriteProbability']:.1%}）；过热={'是' if row['overheatAudit']['detected'] else '否'}；处理：{row['overheatAudit']['action']}。", "",
                  "### 7. 综合结果", "",
                  f"- 90分钟：**{final['result90Minutes']}**，防 {final['protection']}；概率 主{final['probabilities']['home']:.1%} / 平{final['probabilities']['draw']:.1%} / 客{final['probabilities']['away']:.1%}。",
                  f"- 三维加权总分：主 {final['frameworkTotal']['home']} / 客 {final['frameworkTotal']['away']}；晋级字段：{final['advancementLean']}。",
                  f"- 总进球：**{final['totalGoals']}球**；主比分 **{final['mainScore']}**；安全池 {' / '.join(final['safeScores'])}；尾部 {final['tailScore']}；反向冷门 {final['upsetScore']}。",
                  f"- 信心：{final['confidence']}；触发器：{final['riskTrigger']}",
                  f"- 本场采集完整度：{row['completeness']['percent']:.0f}%。", ""]
    lines += ["## 风险说明", "", "数据完整度表示规定的采集动作全部完成并留下来源，不等于样本量充分，更不等于结果必然命中。", "",
              "以上仅为公开信息整理后的娱乐分析，不构成任何购彩建议，请理性参考。", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    args = parser.parse_args()
    prediction = load(ROOT / "output" / f"prediction_{args.date}.json")
    evidence = load(ROOT / "data" / f"evidence_{args.date}.json")
    matches = [build_match(match, evidence) for match in prediction["matches"]]
    payload = {"version": "detailed-three-layer-v1", "businessDate": prediction["businessDate"],
               "generatedAt": radar.utc_now(), "sportteryLastUpdateTime": prediction["sportteryLastUpdateTime"],
               "matches": matches}
    blanks = radar.no_blank_paths(payload)
    payload["audit"] = {"matchCount": len(matches), "completeMatches": sum(
        row["completeness"]["percent"] == 100 for row in matches), "blankValueCount": len(blanks), "blankPaths": blanks}
    if blanks:
        raise SystemExit(f"detailed output contains blanks: {blanks[:20]}")
    radar.write_json(ROOT / "output" / f"detailed_analysis_{args.date}.json", payload)
    (ROOT / "output" / f"detailed_analysis_{args.date}.md").write_text(render(payload), encoding="utf-8")
    print(json.dumps(payload["audit"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
