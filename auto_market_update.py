"""Collect the current Sporttery market snapshot and rebuild the public page."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="Sporttery business date, YYYYMMDD")
    args = parser.parse_args()
    date = args.date or datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
    previous_date = (datetime.strptime(date, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
    previous_page = ROOT / previous_date
    if previous_page.is_dir():
        shutil.rmtree(previous_page)
    python = sys.executable
    commands = [
        [python, str(ROOT / "collect_market_snapshot.py"), "--date", date],
        [python, str(ROOT / "build_sporttery_day.py"), "--date", date],
        [python, str(ROOT / "build_today_page.py"), "--date", date],
    ]
    for command in commands:
        subprocess.run(command, cwd=ROOT, check=True)
    print(f"market update complete: {date}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
