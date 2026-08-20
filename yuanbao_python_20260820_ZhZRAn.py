"""Evidence-first football radar and prediction pipeline.

This replaces the original non-runnable pseudocode.  It deliberately separates
collection completeness from predictive certainty: a 100% collection audit
means every required endpoint and field was checked, never that a forecast is
certain to win.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import re
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
CACHE = DATA / "cache"
OUTPUT = ROOT / "output"
MODEL_VERSION = "football-radar-evidence-first-v2"
SPORTTERY_URL = (
    "https://webapi.sporttery.cn/gateway/uniform/football/"
    "getMatchCalculatorV1.qry?channel=c&poolCode=ttg,had,hhad,crs,hafu"
)
SPORTTERY_HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 Version/18.5 Mobile Safari/604.1",
    "Referer": "https://m.sporttery.cn/mjc/jsq/zqzjq/",
    "Origin": "https://m.sporttery.cn",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
API_BASE = "https://v3.football.api-sports.io/"

# Chinese names are the official Sporttery labels.  The right side is only a
# deterministic identifier bridge to the external fixture provider.
TEAM_ALIASES = {
    "迈季迈阿宽广": "Al-Fayha",
    "利雅得新月": "Al-Hilal Saudi FC",
    "巴列卡诺": "Rayo Vallecano",
    "阿拉维斯": "Alaves",
    "科林蒂安": "Corinthians",
    "罗萨里奥中央": "Rosario Central",
    "阿拉木图凯拉特": "Kairat Almaty",
    "安德莱赫特": "Anderlecht",
    "米亚尔比": "Mjallby AIF",
    "萨尔茨堡": "Red Bull Salzburg",
    "特拉布宗体育": "Trabzonspor",
    "费伦茨瓦罗斯": "Ferencvarosi TC",
    "波兹南莱赫": "Lech Poznan",
    "图恩": "FC Thun",
    "贝尔格莱德红星": "FK Crvena Zvezda",
    "比尔森": "Plzen",
    "本菲卡": "Benfica",
    "奥胡斯": "Aarhus",
}
TEAM_CODE_ALIASES = {
    "AFH": "Al-Fayha", "HLL": "Al-Hilal Saudi FC",
    "VNO": "Rayo Vallecano", "ALS": "Alaves",
    "COR": "Corinthians", "RCL": "Rosario Central",
    "KAT": "Kairat Almaty", "ANT": "Anderlecht",
    "MJB": "Mjallby AIF", "SAL": "Red Bull Salzburg",
    "TRA": "Trabzonspor", "FES": "Ferencvarosi TC",
    "LPZ": "Lech Poznan", "THN": "FC Thun",
    "CRV": "FK Crvena Zvezda", "VKP": "Plzen",
    "BEN": "Benfica", "AGF": "Aarhus",
}
CITY_FALLBACK = {
    "Al-Fayha": "Buraidah",
    "Rayo Vallecano": "Madrid",
    "Corinthians": "Sao Paulo",
    "Kairat Almaty": "Almaty",
    "Mjallby AIF": "Hallevik",
    "Trabzonspor": "Trabzon",
    "Lech Poznan": "Poznan",
    "FK Crvena Zvezda": "Belgrade",
    "Benfica": "Lisbon",
}
AUTHORITY_SOURCES = {
    "UEFA Europa League": "https://www.uefa.com/uefaeuropaleague/accesslist/",
    "CONMEBOL Libertadores": "https://conmebollibertadores.com/",
    "La Liga": "https://www.laliga.com/en-GB/laliga-easports/results",
    "Pro League": "https://www.spl.com.sa/en",
}
ESPN_NAMES = {
    "Al-Fayha": "Al Fayha", "Al-Hilal Saudi FC": "Al Hilal",
    "Rayo Vallecano": "Rayo Vallecano", "Alaves": "Alaves",
    "Corinthians": "Corinthians", "Rosario Central": "Rosario Central",
    "Kairat Almaty": "Kairat Almaty", "Anderlecht": "Anderlecht",
    "Mjallby AIF": "Mjallby AIF", "Red Bull Salzburg": "RB Salzburg",
    "Trabzonspor": "Trabzonspor", "Ferencvarosi TC": "Ferencvaros",
    "Lech Poznan": "Lech Poznan", "FC Thun": "FC Thun",
    "FK Crvena Zvezda": "Red Star Belgrade", "Plzen": "Viktoria Plzen",
    "Benfica": "Benfica", "Aarhus": "AGF",
}
THESPORTSDB_NAMES = {"Plzen": "Viktoria Plzen"}
POOL_FIELDS = {
    "had": ("h", "d", "a"),
    "hhad": ("h", "d", "a", "goalLine", "goalLineValue", "fixedOddsGoal"),
    "ttg": tuple(f"s{i}" for i in range(8)),
    "crs": (),
    "hafu": ("hh", "hd", "ha", "dh", "dd", "da", "ah", "ad", "aa"),
}
FINISHED = {"FT", "AET", "PEN"}


class CollectionError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def clean_secret(value: str | None) -> str:
    return (value or "").strip().strip("'").strip('"').strip()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def cache_file(prefix: str, url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
    return CACHE / prefix / f"{digest}.json"


def http_json(url: str, headers: dict[str, str] | None = None, *, cache: Path | None = None,
              refresh: bool = False, attempts: int = 3) -> dict[str, Any]:
    if cache and cache.exists() and not refresh:
        return read_json(cache)
    error = ""
    for attempt in range(attempts):
        request = Request(url, headers=headers or {"User-Agent": "Mozilla/5.0 football-radar/2.0"})
        try:
            with urlopen(request, timeout=45) as response:
                payload = json.loads(response.read())
            if cache:
                write_json(cache, payload)
            return payload
        except Exception as exc:  # each provider gets bounded retries
            error = f"{type(exc).__name__}: {exc}"
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    raise CollectionError(f"request failed after {attempts} attempts: {url}: {error}")


def api_football(path: str, key: str, refresh: bool) -> dict[str, Any]:
    url = API_BASE + path
    payload = http_json(url, {"x-apisports-key": key, "User-Agent": "Mozilla/5.0"},
                        cache=cache_file("api_football", url), refresh=refresh)
    errors = payload.get("errors")
    if errors and errors != []:
        raise CollectionError(f"API-Football rejected {path}: {errors}")
    return payload


def offered(raw: dict[str, Any], pool: str) -> bool:
    if not isinstance(raw, dict) or not raw:
        return False
    if pool == "crs":
        return any(re.fullmatch(r"s\d{2}s\d{2}", key) and str(value).strip()
                   for key, value in raw.items())
    return all(str(raw.get(field, "")).strip() for field in POOL_FIELDS[pool] if field not in {"fixedOddsGoal"})


def normalize_pool(raw: dict[str, Any] | None, pool: str) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    if not offered(raw, pool):
        return {
            "status": "not_offered_by_official_api",
            "reason": f"中国竞彩网实时响应中未提供 {pool.upper()} 在售值",
            "checkedAt": utc_now(),
            "source": SPORTTERY_URL,
        }
    if pool == "crs":
        values = {key: float(value) for key, value in raw.items()
                  if (re.fullmatch(r"s\d{2}s\d{2}", key) or key in {"s1sh", "s1sd", "s1sa"})
                  and str(value).strip()}
    else:
        values = {field: raw[field] for field in POOL_FIELDS[pool]
                  if field in raw and str(raw[field]).strip()}
        for key in list(values):
            if key not in {"goalLine", "goalLineValue", "fixedOddsGoal"}:
                values[key] = float(values[key])
    updated = " ".join(x for x in (str(raw.get("updateDate", "")).strip(),
                                    str(raw.get("updateTime", "")).strip()) if x)
    return {
        "status": "offered",
        "values": values,
        "updatedAt": updated or "official_response_has_no_pool_timestamp",
        "source": SPORTTERY_URL,
    }


def collect_sporttery(date: str, refresh: bool) -> dict[str, Any]:
    target = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    payload = http_json(SPORTTERY_URL, SPORTTERY_HEADERS, refresh=True)
    if str(payload.get("errorCode")) != "0":
        raise CollectionError(f"Sporttery error: {payload.get('errorMessage') or payload.get('errorCode')}")
    raw_path = DATA / "raw" / f"sporttery_{date}_latest.json"
    write_json(raw_path, payload)
    matches = []
    for group in payload.get("value", {}).get("matchInfoList", []):
        for raw in group.get("subMatchList", []):
            if raw.get("businessDate") != target:
                continue
            match_num = str(raw.get("matchNumStr", "official_match_number_missing"))
            matches.append({
                "matchId": str(raw["matchId"]),
                "matchNumStr": match_num,
                "businessDate": raw["businessDate"],
                "kickoff": f"{raw['matchDate']}T{raw['matchTime']}+08:00",
                "league": raw.get("leagueAllName") or raw.get("leagueAbbName") or "official_league_label_missing",
                "leagueCode": raw.get("leagueCode") or "official_league_code_missing",
                "home": raw.get("homeTeamAllName") or raw.get("homeTeamAbbName"),
                "away": raw.get("awayTeamAllName") or raw.get("awayTeamAbbName"),
                "homeCode": raw.get("homeTeamCode") or "official_team_code_missing",
                "awayCode": raw.get("awayTeamCode") or "official_team_code_missing",
                "matchStatus": raw.get("matchStatus") or "official_status_missing",
                "pools": {name: normalize_pool(raw.get(name), name) for name in POOL_FIELDS},
            })
    if not matches:
        raise CollectionError(f"Sporttery returned zero matches for businessDate={target}")
    result = {
        "date": date,
        "businessDate": target,
        "fetchedAt": utc_now(),
        "lastUpdateTime": payload.get("value", {}).get("lastUpdateTime") or "official_global_timestamp_missing",
        "source": SPORTTERY_URL,
        "rawSnapshot": str(raw_path.relative_to(ROOT)),
        "matches": sorted(matches, key=lambda row: row["matchNumStr"]),
    }
    write_json(DATA / f"sporttery_{date}.json", result)
    return result


def norm(value: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", ascii_text.lower())


def fixture_teams(row: dict[str, Any]) -> tuple[str, str]:
    teams = row.get("teams", {})
    return str(teams.get("home", {}).get("name", "")), str(teams.get("away", {}).get("name", ""))


def match_fixture(match: dict[str, Any], fixtures: list[dict[str, Any]]) -> dict[str, Any]:
    home_alias = TEAM_CODE_ALIASES.get(match["homeCode"]) or TEAM_ALIASES.get(match["home"])
    away_alias = TEAM_CODE_ALIASES.get(match["awayCode"]) or TEAM_ALIASES.get(match["away"])
    if not home_alias or not away_alias:
        raise CollectionError(f"team alias not configured: {match['home']} vs {match['away']}")
    wanted = (norm(home_alias), norm(away_alias))
    exact = [row for row in fixtures if tuple(norm(x) for x in fixture_teams(row)) == wanted]
    if len(exact) != 1:
        candidates = [" vs ".join(fixture_teams(row)) for row in fixtures
                      if norm(home_alias) in norm(" ".join(fixture_teams(row))) or
                      norm(away_alias) in norm(" ".join(fixture_teams(row)))]
        raise CollectionError(f"fixture mapping is not unique for {match['matchNumStr']}: {candidates}")
    return exact[0]


def finished_rows(payload: dict[str, Any], before: datetime) -> list[dict[str, Any]]:
    rows = []
    for row in payload.get("response", []):
        fixture = row.get("fixture", {})
        status = fixture.get("status", {}).get("short")
        date_text = fixture.get("date")
        goals = row.get("goals", {})
        if status not in FINISHED or goals.get("home") is None or goals.get("away") is None or not date_text:
            continue
        when = datetime.fromisoformat(date_text.replace("Z", "+00:00"))
        if when < before.astimezone(timezone.utc):
            rows.append(row)
    return sorted(rows, key=lambda row: row["fixture"]["date"], reverse=True)


def compact_fixture(row: dict[str, Any]) -> dict[str, Any]:
    home, away = fixture_teams(row)
    return {
        "date": row["fixture"]["date"],
        "competition": row.get("league", {}).get("name") or "provider_competition_label_missing",
        "home": home,
        "away": away,
        "homeId": int(row.get("teams", {}).get("home", {}).get("id", -1)),
        "awayId": int(row.get("teams", {}).get("away", {}).get("id", -1)),
        "score": f"{row['goals']['home']}-{row['goals']['away']}",
        "status": row["fixture"]["status"]["short"],
    }


def espn_competitors(event: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = (event.get("competitions") or [{}])[0].get("competitors") or []
    by_side = {row.get("homeAway"): row for row in rows}
    return by_side.get("home", {}), by_side.get("away", {})


def espn_score(competitor: dict[str, Any]) -> int | None:
    value = competitor.get("score")
    if isinstance(value, dict):
        value = value.get("value", value.get("displayValue"))
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def espn_current_event(home_name: str, away_name: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    wanted = (norm(ESPN_NAMES[home_name]), norm(ESPN_NAMES[away_name]))
    matches = []
    for event in events:
        home, away = espn_competitors(event)
        names = (norm(home.get("team", {}).get("displayName", "")),
                 norm(away.get("team", {}).get("displayName", "")))
        if names == wanted:
            matches.append(event)
    if len(matches) != 1:
        raise CollectionError(f"ESPN current fixture mapping failed: {home_name} vs {away_name}; matches={len(matches)}")
    return matches[0]


def compact_espn_history(event: dict[str, Any], target_espn_id: int,
                         target_api_id: int, source: str) -> dict[str, Any] | None:
    home, away = espn_competitors(event)
    home_score, away_score = espn_score(home), espn_score(away)
    if home_score is None or away_score is None or not event.get("date"):
        return None
    home_espn = int(home.get("team", {}).get("id", -1))
    away_espn = int(away.get("team", {}).get("id", -1))
    if target_espn_id not in {home_espn, away_espn}:
        return None
    return {
        "date": event["date"],
        "competition": event.get("league", {}).get("name") or "ESPN cross-competition team schedule",
        "home": home.get("team", {}).get("displayName") or "ESPN home label missing",
        "away": away.get("team", {}).get("displayName") or "ESPN away label missing",
        "homeId": target_api_id if home_espn == target_espn_id else -1,
        "awayId": target_api_id if away_espn == target_espn_id else -1,
        "score": f"{home_score}-{away_score}",
        "status": "completed_score_recorded",
        "source": source,
    }


def thesportsdb_recent(api_team: dict[str, Any], kickoff: datetime,
                       refresh: bool) -> tuple[list[dict[str, Any]], str]:
    query_name = THESPORTSDB_NAMES.get(api_team["name"], api_team["name"])
    search_url = "https://www.thesportsdb.com/api/v1/json/3/searchteams.php?" + urlencode({"t": query_name})
    search = http_json(search_url, cache=cache_file("thesportsdb", search_url), refresh=refresh)
    candidates = [row for row in (search.get("teams") or []) if row.get("strSport") == "Soccer"
                  and norm(row.get("strTeam", "")) == norm(query_name)]
    if len(candidates) != 1:
        raise CollectionError(f"TheSportsDB team mapping failed for {query_name}: {len(candidates)} matches")
    team = candidates[0]
    url = f"https://www.thesportsdb.com/api/v1/json/3/eventslast.php?id={team['idTeam']}"
    payload = http_json(url, cache=cache_file("thesportsdb", url), refresh=refresh)
    rows = []
    for event in payload.get("results") or []:
        if event.get("intHomeScore") is None or event.get("intAwayScore") is None:
            continue
        when = datetime.fromisoformat((event.get("strTimestamp") or event.get("dateEvent") + "T00:00:00")
                                      .replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when >= kickoff:
            continue
        is_home = norm(event.get("strHomeTeam", "")) == norm(team["strTeam"])
        is_away = norm(event.get("strAwayTeam", "")) == norm(team["strTeam"])
        if not is_home and not is_away:
            continue
        rows.append({
            "date": when.isoformat(),
            "competition": event.get("strLeague") or "TheSportsDB competition label missing",
            "home": event.get("strHomeTeam") or "TheSportsDB home label missing",
            "away": event.get("strAwayTeam") or "TheSportsDB away label missing",
            "homeId": int(api_team["id"]) if is_home else -1,
            "awayId": int(api_team["id"]) if is_away else -1,
            "score": f"{int(event['intHomeScore'])}-{int(event['intAwayScore'])}",
            "status": event.get("strStatus") or "completed_score_recorded",
            "source": url,
        })
    rows.sort(key=lambda item: item["date"], reverse=True)
    return rows[:8], url


def collect_espn_recent(sporttery: dict[str, Any], api_fixtures: dict[str, dict[str, Any]],
                        refresh: bool) -> dict[str, Any]:
    utc_dates = sorted({datetime.fromisoformat(row["fixture"]["date"].replace("Z", "+00:00")).strftime("%Y%m%d")
                        for row in api_fixtures.values()})
    current_events = []
    for date_text in utc_dates:
        url = f"https://site.web.api.espn.com/apis/site/v2/sports/soccer/all/scoreboard?dates={date_text}&limit=1000"
        payload = http_json(url, cache=cache_file("espn", url), refresh=refresh)
        current_events.extend(payload.get("events") or [])
    plans = []
    for match in sporttery["matches"]:
        fixture = api_fixtures[match["matchId"]]
        home_api = fixture["teams"]["home"]
        away_api = fixture["teams"]["away"]
        event = espn_current_event(home_api["name"], away_api["name"], current_events)
        home, away = espn_competitors(event)
        for side, api_team, competitor in (("home", home_api, home), ("away", away_api, away)):
            espn_id = int(competitor["team"]["id"])
            url = f"https://site.web.api.espn.com/apis/site/v2/sports/soccer/all/teams/{espn_id}/schedule?season=2026"
            plans.append((match["matchId"], side, api_team, espn_id, url))
    payloads: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        future_map = {pool.submit(http_json, url, cache=cache_file("espn", url), refresh=refresh): url
                      for *_, url in plans}
        for future in as_completed(future_map):
            url = future_map[future]
            payloads[url] = future.result()
    output: dict[str, Any] = {match["matchId"]: {} for match in sporttery["matches"]}
    for match_id, side, api_team, espn_id, url in plans:
        kickoff = datetime.fromisoformat(api_fixtures[match_id]["fixture"]["date"].replace("Z", "+00:00"))
        rows = []
        for event in payloads[url].get("events") or []:
            event_date = datetime.fromisoformat(str(event.get("date", "1900-01-01T00:00Z")).replace("Z", "+00:00"))
            if event_date >= kickoff:
                continue
            row = compact_espn_history(event, espn_id, int(api_team["id"]), url)
            if row:
                rows.append(row)
        rows.sort(key=lambda item: item["date"], reverse=True)
        if not rows:
            rows, url = thesportsdb_recent(api_team, kickoff, refresh)
        if not rows:
            raise CollectionError(f"no completed cross-competition history for {api_team['name']}")
        output[match_id][side] = rows[:8]
        output[match_id][side + "Source"] = url
    return output


def collect_weather(city: str, kickoff: str, refresh: bool) -> dict[str, Any]:
    geo_url = "https://geocoding-api.open-meteo.com/v1/search?" + urlencode({
        "name": city, "count": 1, "language": "en", "format": "json"})
    geo = http_json(geo_url, cache=cache_file("weather", geo_url), refresh=refresh)
    results = geo.get("results") or []
    if not results:
        raise CollectionError(f"weather geocoding returned no location for {city}")
    location = results[0]
    dt = datetime.fromisoformat(kickoff.replace("Z", "+00:00")).astimezone(timezone.utc)
    start_date = (dt - timedelta(days=1)).date().isoformat()
    end_date = (dt + timedelta(days=1)).date().isoformat()
    forecast_url = "https://api.open-meteo.com/v1/forecast?" + urlencode({
        "latitude": location["latitude"], "longitude": location["longitude"],
        "hourly": "temperature_2m,precipitation_probability,wind_speed_10m",
        "timezone": "auto", "start_date": start_date, "end_date": end_date,
    })
    forecast = http_json(forecast_url, cache=cache_file("weather", forecast_url), refresh=refresh)
    hourly = forecast.get("hourly", {})
    if not hourly.get("time"):
        raise CollectionError(f"weather forecast has no hourly rows for {city}")
    local_tz = timezone(timedelta(seconds=int(forecast.get("utc_offset_seconds", 0))))
    local_dt = dt.astimezone(local_tz)
    index = min(range(len(hourly["time"])), key=lambda i: abs(
        datetime.fromisoformat(hourly["time"][i]).replace(tzinfo=local_tz).timestamp() - dt.timestamp()))
    return {
        "status": "checked",
        "location": f"{location['name']}, {location.get('country_code') or location.get('country')}",
        "forecastLocalHour": hourly["time"][index],
        "temperatureC": hourly["temperature_2m"][index],
        "precipitationProbabilityPct": hourly["precipitation_probability"][index],
        "windSpeedKmh": hourly["wind_speed_10m"][index],
        "source": forecast_url,
        "checkedAt": utc_now(),
    }


def checked_result(payload: dict[str, Any], source: str) -> dict[str, Any]:
    return {
        "status": "checked",
        "recordCount": len(payload.get("response") or []),
        "source": source,
        "checkedAt": utc_now(),
    }


def collect_external(sporttery: dict[str, Any], key: str, refresh: bool) -> dict[str, Any]:
    if not key:
        raise CollectionError("API_FOOTBALL_KEY is required; predictions are blocked without external evidence")
    dates = sorted({row["kickoff"][:10] for row in sporttery["matches"]})
    fixture_rows = []
    fixture_sources = []
    for date_text in dates:
        path = f"fixtures?date={date_text}"
        payload = api_football(path, key, refresh)
        fixture_rows.extend(payload.get("response") or [])
        fixture_sources.append(API_BASE + path)
    fixture_by_match = {match["matchId"]: match_fixture(match, fixture_rows)
                        for match in sporttery["matches"]}
    espn_recent = collect_espn_recent(sporttery, fixture_by_match, refresh)
    output: dict[str, Any] = {"date": sporttery["date"], "generatedAt": utc_now(), "matches": {}}
    for match in sporttery["matches"]:
        fixture = fixture_by_match[match["matchId"]]
        fixture_id = int(fixture["fixture"]["id"])
        home_team = fixture["teams"]["home"]
        away_team = fixture["teams"]["away"]
        kickoff = datetime.fromisoformat(fixture["fixture"]["date"].replace("Z", "+00:00"))
        endpoints = {
            "injuries": f"injuries?fixture={fixture_id}",
            "h2h": f"fixtures/headtohead?h2h={home_team['id']}-{away_team['id']}",
            "odds": f"odds?fixture={fixture_id}",
        }
        payloads = {name: api_football(path, key, refresh) for name, path in endpoints.items()}
        home_recent = espn_recent[match["matchId"]]["home"]
        away_recent = espn_recent[match["matchId"]]["away"]
        if not home_recent or not away_recent:
            raise CollectionError(f"recent completed-match evidence missing for {match['matchNumStr']}")
        venue = fixture["fixture"].get("venue") or {}
        city = venue.get("city") or CITY_FALLBACK.get(home_team["name"])
        if not city:
            raise CollectionError(f"venue city could not be resolved for {match['matchNumStr']}")
        weather = collect_weather(city, fixture["fixture"]["date"], refresh)
        injuries = payloads["injuries"].get("response") or []
        injury_rows_raw = [{
            "team": row.get("team", {}).get("name") or "provider_team_label_missing",
            "player": row.get("player", {}).get("name") or "provider_player_label_missing",
            "type": row.get("player", {}).get("type") or "provider_injury_type_missing",
            "reason": row.get("player", {}).get("reason") or "provider_injury_reason_missing",
        } for row in injuries]
        injury_rows = list({(row["team"], row["player"], row["type"], row["reason"]): row
                            for row in injury_rows_raw}.values())
        source_urls = {name: API_BASE + path for name, path in endpoints.items()}
        output["matches"][match["matchId"]] = {
            "fixture": {
                "status": "matched",
                "fixtureId": fixture_id,
                "kickoffUtc": fixture["fixture"]["date"],
                "competition": fixture["league"].get("name") or "provider_competition_label_missing",
                "round": fixture["league"].get("round") or "provider_round_label_missing",
                "season": fixture["league"].get("season"),
                "home": home_team["name"], "away": away_team["name"],
                "homeId": home_team["id"], "awayId": away_team["id"],
                "venue": venue.get("name") or f"city-centred weather location: {city}",
                "city": city,
                "referee": fixture["fixture"].get("referee") or "pre-match referee field checked with no value",
                "source": fixture_sources[0] if len(fixture_sources) == 1 else fixture_sources,
            },
            "recent": {"home": home_recent, "away": away_recent,
                       "homeSource": espn_recent[match["matchId"]]["homeSource"],
                       "awaySource": espn_recent[match["matchId"]]["awaySource"]},
            "injuries": {**checked_result(payloads["injuries"], source_urls["injuries"]),
                         "recordCount": len(injury_rows), "records": injury_rows},
            "headToHead": {**checked_result(payloads["h2h"], source_urls["h2h"]),
                           "records": [compact_fixture(x) for x in finished_rows(payloads["h2h"], kickoff)]},
            "externalOdds": {**checked_result(payloads["odds"], source_urls["odds"]),
                             "records": payloads["odds"].get("response") or []},
            "weather": weather,
            "competitionAuthority": AUTHORITY_SOURCES.get(fixture["league"].get("name"), "https://www.sporttery.cn/"),
        }
    write_json(DATA / f"evidence_{sporttery['date']}.json", output)
    return output


def implied(odds: list[float]) -> list[float]:
    raw = [1.0 / value for value in odds]
    total = sum(raw)
    return [value / total for value in raw]


def market_probabilities(match: dict[str, Any]) -> tuple[dict[str, float], str]:
    had = match["pools"]["had"]
    if had["status"] == "offered":
        values = had["values"]
        probs = implied([values["h"], values["d"], values["a"]])
        return dict(zip(("home", "draw", "away"), probs)), "HAD"
    crs = match["pools"]["crs"]
    buckets = {"home": 0.0, "draw": 0.0, "away": 0.0}
    for key, odd in crs["values"].items():
        if re.fullmatch(r"s\d{2}s\d{2}", key):
            home_goals, away_goals = int(key[1:3]), int(key[4:6])
            outcome = "home" if home_goals > away_goals else "away" if home_goals < away_goals else "draw"
        else:
            outcome = {"s1sh": "home", "s1sd": "draw", "s1sa": "away"}[key]
        buckets[outcome] += 1.0 / float(odd)
    total = sum(buckets.values())
    if total <= 0:
        raise CollectionError(f"cannot derive 1X2 market for {match['matchNumStr']}")
    return {key: value / total for key, value in buckets.items()}, "CRS-outcome-marginal"


def form_summary(team_id: int, rows: list[dict[str, Any]]) -> dict[str, Any]:
    wins = draws = losses = goals_for = goals_against = 0
    dates = []
    trail = []
    for row in rows:
        home_id = int(row.get("homeId", row.get("teams", {}).get("home", {}).get("id", -1)))
        away_id = int(row.get("awayId", row.get("teams", {}).get("away", {}).get("id", -1)))
        if team_id not in {home_id, away_id}:
            raise CollectionError(f"recent-form row is not mapped to team {team_id}: {row}")
        score = row.get("score", "0-0")
        hg, ag = (int(x) for x in score.split("-"))
        is_home = team_id == home_id
        gf, ga = (hg, ag) if is_home else (ag, hg)
        goals_for += gf; goals_against += ga
        if gf > ga: wins += 1; trail.append("W")
        elif gf == ga: draws += 1; trail.append("D")
        else: losses += 1; trail.append("L")
        dates.append(row["date"])
    count = len(rows)
    return {
        "matches": count, "wins": wins, "draws": draws, "losses": losses,
        "goalsFor": goals_for, "goalsAgainst": goals_against,
        "pointsPerGame": round((wins * 3 + draws) / count, 3),
        "goalDifferencePerGame": round((goals_for - goals_against) / count, 3),
        "recentTrailNewestFirst": "-".join(trail),
        "lastCompletedAt": dates[0],
    }


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def outcome_from_score(score: str) -> str:
    h, a = (int(x) for x in score.split("-"))
    return "home" if h > a else "away" if h < a else "draw"


def score_market(match: dict[str, Any]) -> list[tuple[str, float]]:
    values = match["pools"]["crs"]["values"]
    rows = []
    for key, odd in values.items():
        if not re.fullmatch(r"s\d{2}s\d{2}", key):
            continue
        rows.append((f"{int(key[1:3])}-{int(key[4:6])}", 1.0 / float(odd)))
    total = sum(weight for _, weight in rows)
    return [(score, weight / total) for score, weight in rows]


def total_market(match: dict[str, Any]) -> dict[int, float]:
    values = match["pools"]["ttg"]["values"]
    probs = implied([float(values[f"s{i}"]) for i in range(8)])
    return {i: probs[i] for i in range(8)}


def competition_mode(external: dict[str, Any]) -> str:
    name = external["fixture"]["competition"]
    round_name = external["fixture"]["round"].lower()
    h2h = external["headToHead"]["records"]
    if "play-off" in round_name or "round of 16" in round_name:
        current = datetime.fromisoformat(external["fixture"]["kickoffUtc"].replace("Z", "+00:00"))
        recent_same_pair = [row for row in h2h if 0 < (current - datetime.fromisoformat(
            row["date"].replace("Z", "+00:00"))).days <= 21]
        return "two_leg_second" if recent_same_pair else "two_leg_first"
    if any(word in round_name for word in ("final", "semi-final")):
        return "cup_single"
    return "league"


def calculate_prediction(match: dict[str, Any], external: dict[str, Any]) -> dict[str, Any]:
    home_id = external["fixture"]["homeId"]
    away_id = external["fixture"]["awayId"]
    home_form = form_summary(home_id, external["recent"]["home"])
    away_form = form_summary(away_id, external["recent"]["away"])
    home_injuries = sum(1 for row in external["injuries"]["records"] if row["team"] == external["fixture"]["home"])
    away_injuries = sum(1 for row in external["injuries"]["records"] if row["team"] == external["fixture"]["away"])
    market, market_source = market_probabilities(match)
    market_gap = (market["home"] - market["away"]) * 100

    home_reliability = min(1.0, home_form["matches"] / 5.0)
    away_reliability = min(1.0, away_form["matches"] / 5.0)
    hard_home = clamp(50 + ((home_form["pointsPerGame"] - 1.5) * 16 + home_form["goalDifferencePerGame"] * 6) * home_reliability)
    hard_away = clamp(50 + ((away_form["pointsPerGame"] - 1.5) * 16 + away_form["goalDifferencePerGame"] * 6) * away_reliability)
    tactical_home = clamp(50 + (home_form["goalsFor"] / home_form["matches"] - away_form["goalsAgainst"] / away_form["matches"]) * 8 - home_injuries * 2)
    tactical_away = clamp(50 + (away_form["goalsFor"] / away_form["matches"] - home_form["goalsAgainst"] / home_form["matches"]) * 8 - away_injuries * 2)
    kickoff = datetime.fromisoformat(external["fixture"]["kickoffUtc"].replace("Z", "+00:00"))
    home_last = datetime.fromisoformat(home_form["lastCompletedAt"].replace("Z", "+00:00"))
    away_last = datetime.fromisoformat(away_form["lastCompletedAt"].replace("Z", "+00:00"))
    home_rest = max(0, (kickoff - home_last).days)
    away_rest = max(0, (kickoff - away_last).days)
    psychology_home = clamp(50 + (home_form["pointsPerGame"] - 1.5) * 10 * home_reliability + (home_rest - away_rest) * 1.5)
    psychology_away = clamp(50 + (away_form["pointsPerGame"] - 1.5) * 10 * away_reliability + (away_rest - home_rest) * 1.5)
    home_score = hard_home * .40 + tactical_home * .35 + psychology_home * .25
    away_score = hard_away * .40 + tactical_away * .35 + psychology_away * .25
    evidence_gap = home_score - away_score + 4.0
    evidence_draw = clamp(.30 - abs(evidence_gap) * .004, .20, .38)
    evidence_home_share = 1 / (1 + math.exp(-evidence_gap / 12))
    evidence_probs = {
        "home": (1 - evidence_draw) * evidence_home_share,
        "draw": evidence_draw,
        "away": (1 - evidence_draw) * (1 - evidence_home_share),
    }
    blended = {key: market[key] * .72 + evidence_probs[key] * .28 for key in market}
    mode = competition_mode(external)
    corrections = []
    if mode == "two_leg_first":
        draw_add = min(.045, .40 - blended["draw"])
        side_total = blended["home"] + blended["away"]
        blended["draw"] += draw_add
        blended["home"] -= draw_add * blended["home"] / side_total
        blended["away"] -= draw_add * blended["away"] / side_total
        corrections.append("两回合首回合：提高平局与低节奏路径权重")
    overheat = max(market.values()) >= .68 and abs(evidence_gap) + 8 < abs(market_gap)
    if overheat:
        favorite = max(market, key=market.get)
        penalty = min(.04, (max(market.values()) - .68) * .25 + .015)
        blended[favorite] -= penalty
        for key in blended:
            if key != favorite:
                blended[key] += penalty / 2
        corrections.append("市场热门与近期证据差距偏大：执行热门降温")
    norm_total = sum(blended.values())
    blended = {key: value / norm_total for key, value in blended.items()}

    scored = []
    for score, base_prob in score_market(match):
        outcome = outcome_from_score(score)
        market_outcome = max(market[outcome], .001)
        adjusted = base_prob * blended[outcome] / market_outcome
        goals = sum(int(x) for x in score.split("-"))
        if mode == "two_leg_first" and goals >= 4:
            adjusted *= .86
        scored.append((score, adjusted))
    scored.sort(key=lambda row: row[1], reverse=True)
    direction = max(blended, key=blended.get)
    protection = sorted(blended, key=blended.get, reverse=True)[1]
    framework_leader = "home" if home_score >= away_score else "away"
    framework_gap = abs(home_score - away_score)
    market_evidence_conflict = direction in {"home", "away"} and direction != framework_leader and framework_gap >= 5.0
    if market_evidence_conflict:
        protection = framework_leader
        corrections.append(
            f"盘口主方向与三维证据冲突：保护{framework_leader}，三维分差{framework_gap:.2f}"
        )
    direction_scores = [score for score, _ in scored if outcome_from_score(score) == direction]
    protection_scores = [score for score, _ in scored if outcome_from_score(score) == protection]
    if not direction_scores:
        raise CollectionError(f"score market has no exact score for predicted outcome {direction}")
    selected = []
    for score in [direction_scores[0], *(protection_scores[:1]), *direction_scores[1:3],
                  *(score for score, _ in scored)]:
        if score not in selected:
            selected.append(score)
        if len(selected) == 4:
            break
    opposite = "away" if direction == "home" else "home" if direction == "away" else min(
        ("home", "away"), key=blended.get)
    upset = next((score for score, _ in scored if outcome_from_score(score) == opposite), selected[-1])
    totals = total_market(match)
    total_goals = max(totals, key=totals.get)
    probability_margin = sorted(blended.values(), reverse=True)[0] - sorted(blended.values(), reverse=True)[1]
    confidence = "中" if probability_margin < .16 else "较高"
    if probability_margin < .07:
        confidence = "低"
    return {
        "modelVersion": MODEL_VERSION,
        "marketProbabilitySource": market_source,
        "competitionMode": mode,
        "layers": {
            "hardPower": {"home": round(hard_home, 2), "away": round(hard_away, 2), "weight": .40},
            "tacticalMatchup": {"home": round(tactical_home, 2), "away": round(tactical_away, 2), "weight": .35},
            "psychologyAndSchedule": {"home": round(psychology_home, 2), "away": round(psychology_away, 2), "weight": .25},
        },
        "evidence": {"homeForm": home_form, "awayForm": away_form,
                     "homeRestDays": home_rest, "awayRestDays": away_rest,
                     "homeInjuryRecords": home_injuries, "awayInjuryRecords": away_injuries},
        "marketProbabilities": {key: round(value, 4) for key, value in market.items()},
        "finalProbabilities": {key: round(value, 4) for key, value in blended.items()},
        "overheatDetected": overheat,
        "frameworkLeader": framework_leader,
        "frameworkGap": round(framework_gap, 2),
        "marketEvidenceConflict": market_evidence_conflict,
        "corrections": corrections or ["未触发额外赛制或热门修正"],
        "direction": direction,
        "protection": protection,
        "totalGoalsMode": "7+" if total_goals == 7 else str(total_goals),
        "mainScore": selected[0],
        "safeScores": selected[:3],
        "tailScore": selected[3],
        "upsetScore": upset,
        "confidence": confidence,
    }


def completeness(match: dict[str, Any], ext: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "officialSchedule": all(match.get(key) for key in ("matchId", "matchNumStr", "businessDate", "kickoff", "home", "away")),
        "allOfficialPoolsAudited": set(match.get("pools", {})) == set(POOL_FIELDS),
        "externalFixtureMatched": ext.get("fixture", {}).get("status") == "matched",
        "homeRecentCompletedMatches": bool(ext.get("recent", {}).get("home")),
        "awayRecentCompletedMatches": bool(ext.get("recent", {}).get("away")),
        "injuryEndpointChecked": ext.get("injuries", {}).get("status") == "checked",
        "headToHeadEndpointChecked": ext.get("headToHead", {}).get("status") == "checked",
        "externalOddsEndpointChecked": ext.get("externalOdds", {}).get("status") == "checked",
        "weatherChecked": ext.get("weather", {}).get("status") == "checked",
        "competitionAuthorityAttached": str(ext.get("competitionAuthority", "")).startswith("http"),
    }
    passed = sum(bool(value) for value in checks.values())
    return {"percent": round(passed / len(checks) * 100, 2), "passed": passed,
            "total": len(checks), "checks": checks}


def public_external(ext: dict[str, Any]) -> dict[str, Any]:
    """Keep auditable summaries in final output, while raw provider payloads stay cached."""
    compact = {
        "fixture": ext["fixture"],
        "recent": ext["recent"],
        "injuries": ext["injuries"],
        "headToHead": ext["headToHead"],
        "weather": ext["weather"],
        "competitionAuthority": ext["competitionAuthority"],
    }
    for key in ("externalOdds",):
        compact[key] = {field: ext[key][field] for field in
                        ("status", "recordCount", "source", "checkedAt")}
    return compact


def no_blank_paths(value: Any, path: str = "$") -> list[str]:
    bad = []
    if value is None or value == "":
        return [path]
    if isinstance(value, dict):
        for key, child in value.items():
            bad.extend(no_blank_paths(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            bad.extend(no_blank_paths(child, f"{path}[{index}]"))
    return bad


LABEL = {"home": "主胜", "draw": "平", "away": "客胜"}


def build_report(payload: dict[str, Any]) -> str:
    lines = [f"# 足球雷达 · {payload['businessDate']}（竞彩业务日）", "",
             f"生成时间：{payload['generatedAt']}", "",
             f"官方竞彩快照：{payload['sportteryLastUpdateTime']}", "",
             f"完整度：{payload['audit']['completeMatches']}/{payload['audit']['matchCount']} 场通过全部必需检查；空值审计 {payload['audit']['blankValueCount']}。", ""]
    for row in payload["matches"]:
        p, e, x = row["prediction"], row["prediction"]["evidence"], row["external"]
        had = row["officialPools"]["had"]
        had_text = (f"{had['values']['h']:.2f}/{had['values']['d']:.2f}/{had['values']['a']:.2f}"
                    if had["status"] == "offered" else "该玩法未开售；方向概率由官方比分赔率边际化计算")
        injuries = x["injuries"]
        injury_text = ("；".join(f"{item['team']} {item['player']}（{item['type']}，{item['reason']}）" for item in injuries["records"])
                       if injuries["recordCount"] else f"已查询 fixture={x['fixture']['fixtureId']} 的伤停端点，返回 0 条")
        lines += [f"## {row['matchNumStr']} {row['home']} vs {row['away']}", "",
                  f"- 开赛：{row['kickoff']}；{x['fixture']['competition']} / {x['fixture']['round']}；{x['fixture']['venue']}。",
                  f"- 结论：{LABEL[p['direction']]}，防 {LABEL[p['protection']]}；总进球 {p['totalGoalsMode']}；主比分 {p['mainScore']}；安全池 {' / '.join(p['safeScores'])}；尾部 {p['tailScore']}；冷门比分 {p['upsetScore']}。",
                  f"- 概率：主 {p['finalProbabilities']['home']:.1%} / 平 {p['finalProbabilities']['draw']:.1%} / 客 {p['finalProbabilities']['away']:.1%}；信心 {p['confidence']}。",
                  f"- 近期：主队近 {e['homeForm']['matches']} 场 {e['homeForm']['wins']}胜{e['homeForm']['draws']}平{e['homeForm']['losses']}负，{e['homeForm']['goalsFor']}-{e['homeForm']['goalsAgainst']}；客队近 {e['awayForm']['matches']} 场 {e['awayForm']['wins']}胜{e['awayForm']['draws']}平{e['awayForm']['losses']}负，{e['awayForm']['goalsFor']}-{e['awayForm']['goalsAgainst']}。",
                  f"- 赛程：休息 {e['homeRestDays']} 天 vs {e['awayRestDays']} 天；赛制 {p['competitionMode']}；{'；'.join(p['corrections'])}。",
                  f"- 伤停核验：{injury_text}。",
                  f"- 天气：{x['weather']['location']}，{x['weather']['forecastLocalHour']} 约 {x['weather']['temperatureC']}°C，降水概率 {x['weather']['precipitationProbabilityPct']}%，风速 {x['weather']['windSpeedKmh']} km/h。",
                  f"- 官方胜平负：{had_text}；本场采集完整度 {row['completeness']['percent']:.0f}%。",
                  f"- 来源：[中国竞彩网]({SPORTTERY_URL}) · [赛事/伤停/近期数据]({x['injuries']['source']}) · [赛事主管机构]({x['competitionAuthority']}) · [天气]({x['weather']['source']})", ""]
    lines += ["## 说明", "", "“100%完整度”只表示本项目规定的赛前采集项全部执行并留下来源；接口返回 0 条会如实记录，绝不等同于确认无人伤停。预测是概率排序，不可能保证比赛结果。", "", "以上仅为公开信息整理后的娱乐分析，不构成任何购彩建议，请理性参考。", ""]
    return "\n".join(lines)


def build_html(payload: dict[str, Any]) -> str:
    cards = []
    for row in payload["matches"]:
        p, x = row["prediction"], row["external"]
        cards.append(f"""<article class="card"><div class="top"><span>{html.escape(row['matchNumStr'])}</span><b>{row['completeness']['percent']:.0f}% 已核验</b></div>
<h2>{html.escape(row['home'])} <small>vs</small> {html.escape(row['away'])}</h2>
<p class="meta">{html.escape(row['kickoff'])} · {html.escape(x['fixture']['competition'])} · {html.escape(x['fixture']['round'])}</p>
<div class="pick"><strong>{LABEL[p['direction']]}</strong><span>防 {LABEL[p['protection']]}</span><span>{p['totalGoalsMode']} 球</span><span>{p['mainScore']}</span></div>
<p>概率 主 {p['finalProbabilities']['home']:.1%} / 平 {p['finalProbabilities']['draw']:.1%} / 客 {p['finalProbabilities']['away']:.1%}</p>
<p>比分池 {' · '.join(p['safeScores'])}；尾部 {p['tailScore']}；冷门 {p['upsetScore']}</p>
<p>伤停接口记录 {x['injuries']['recordCount']} 条 · 交锋记录 {x['headToHead']['recordCount']} 条 · 外部公司赔率记录 {x['externalOdds']['recordCount']} 条</p>
<details><summary>证据与来源</summary><p><a href="{html.escape(x['injuries']['source'])}">API-Football</a> · <a href="{html.escape(x['competitionAuthority'])}">赛事主管机构</a> · <a href="{html.escape(SPORTTERY_URL)}">中国竞彩网</a></p></details></article>""")
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>足球雷达 {payload['businessDate']}</title><style>
:root{{--ink:#14201c;--paper:#f5f0e6;--green:#0f5b46;--lime:#c8ef65;--line:#cabfae}}*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.6 system-ui,"Microsoft YaHei",sans-serif}}header,main,footer{{max-width:1120px;margin:auto;padding:28px}}header{{padding-top:64px}}h1{{font:800 clamp(42px,8vw,86px)/.95 Georgia,serif;margin:8px 0}}.eyebrow{{letter-spacing:.16em;color:var(--green);font-weight:800}}.audit{{display:inline-block;background:var(--lime);padding:8px 13px;border:1px solid var(--ink);box-shadow:4px 4px 0 var(--ink)}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:18px}}.card{{background:#fff;border:1px solid var(--line);padding:22px;box-shadow:7px 7px 0 #dcd2c2}}.top,.pick{{display:flex;gap:10px;justify-content:space-between;flex-wrap:wrap}}.top b{{color:var(--green)}}h2{{margin:14px 0 0;font-size:25px}}h2 small{{font-size:13px;color:#786f63}}.meta{{color:#6a6258}}.pick{{background:var(--green);color:#fff;padding:13px}}.pick strong{{color:var(--lime);font-size:22px}}a{{color:var(--green)}}footer{{border-top:1px solid var(--line);margin-top:30px}}@media(max-width:600px){{header,main,footer{{padding:20px}}}}
</style></head><body><header><p class="eyebrow">SPORTTERY · EVIDENCE FIRST</p><h1>足球雷达</h1><p>{payload['businessDate']} 竞彩业务日 · {len(payload['matches'])} 场</p><p class="audit">{payload['audit']['completeMatches']}/{payload['audit']['matchCount']} 场全部必需采集检查通过</p></header><main class="grid">{''.join(cards)}</main><footer><p>100% 指采集审计完整，不代表命中率。以上仅为公开信息整理后的娱乐分析，不构成任何购彩建议，请理性参考。</p></footer></body></html>"""


def generate(date: str, refresh: bool) -> dict[str, Any]:
    sporttery = collect_sporttery(date, refresh)
    external = collect_external(sporttery, clean_secret(os.getenv("API_FOOTBALL_KEY")), refresh)
    rows = []
    for match in sporttery["matches"]:
        ext = external["matches"][match["matchId"]]
        coverage = completeness(match, ext)
        if coverage["percent"] != 100.0:
            failed = [key for key, value in coverage["checks"].items() if not value]
            raise CollectionError(f"prediction blocked for {match['matchNumStr']}; failed checks: {failed}")
        rows.append({
            "matchId": match["matchId"], "matchNumStr": match["matchNumStr"],
            "businessDate": match["businessDate"], "kickoff": match["kickoff"],
            "league": match["league"], "home": match["home"], "away": match["away"],
            "officialPools": match["pools"], "external": public_external(ext),
            "prediction": calculate_prediction(match, ext), "completeness": coverage,
        })
    payload = {
        "modelVersion": MODEL_VERSION, "businessDate": sporttery["businessDate"],
        "generatedAt": utc_now(), "sportteryLastUpdateTime": sporttery["lastUpdateTime"],
        "source": sporttery["source"], "matches": rows,
    }
    blanks = no_blank_paths(payload)
    payload["audit"] = {
        "matchCount": len(rows),
        "completeMatches": sum(row["completeness"]["percent"] == 100 for row in rows),
        "blankValueCount": len(blanks), "blankPaths": blanks,
        "definition": "all mandatory collection checks passed; this is not predictive certainty",
    }
    if blanks:
        raise CollectionError(f"final output contains blank values: {blanks[:20]}")
    write_json(OUTPUT / f"prediction_{date}.json", payload)
    (OUTPUT / f"prediction_{date}.md").write_text(build_report(payload), encoding="utf-8")
    page_dir = ROOT / date
    page_dir.mkdir(parents=True, exist_ok=True)
    (page_dir / "index.html").write_text(build_html(payload), encoding="utf-8")
    (ROOT / "index.html").write_text(build_html(payload), encoding="utf-8")
    return payload


def audit(date: str) -> dict[str, Any]:
    path = OUTPUT / f"prediction_{date}.json"
    if not path.exists():
        raise CollectionError(f"prediction output not found: {path}")
    payload = read_json(path)
    failures = []
    if payload.get("audit", {}).get("blankValueCount") != 0:
        failures.append("blank values exist")
    for row in payload.get("matches", []):
        if row.get("completeness", {}).get("percent") != 100:
            failures.append(f"{row.get('matchNumStr')} completeness is below 100")
        if set(row.get("officialPools", {})) != set(POOL_FIELDS):
            failures.append(f"{row.get('matchNumStr')} pool audit is incomplete")
        if not row.get("prediction", {}).get("safeScores"):
            failures.append(f"{row.get('matchNumStr')} score pool is empty")
    return {"date": date, "passed": not failures, "matchCount": len(payload.get("matches", [])),
            "failures": failures or ["none"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Evidence-first Sporttery football radar")
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run", help="collect, predict, render and audit")
    run_parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    run_parser.add_argument("--refresh", action="store_true", help="refresh external provider caches")
    audit_parser = sub.add_parser("audit", help="audit an existing generated prediction")
    audit_parser.add_argument("--date", required=True)
    args = parser.parse_args()
    try:
        if args.command == "run":
            payload = generate(args.date, args.refresh)
            result = audit(args.date)
            print(json.dumps({"generated": len(payload["matches"]), "audit": result,
                              "output": str(OUTPUT / f"prediction_{args.date}.json")}, ensure_ascii=False))
        else:
            result = audit(args.date)
            print(json.dumps(result, ensure_ascii=False))
            if not result["passed"]:
                return 2
        return 0
    except CollectionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
