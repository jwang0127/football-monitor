import argparse, json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument('--date', required=True)
args = parser.parse_args()
snapshot = ROOT / 'data' / f'market_snapshots_{args.date}.jsonl'
rows = [json.loads(line) for line in snapshot.read_text(encoding='utf-8').splitlines() if line.strip()]
latest = {}
for row in rows:
    latest[row['matchNum']] = row
matches = []
for row in sorted(latest.values(), key=lambda item: item['matchNum']):
    matches.append({
        'matchId': row.get('matchId', 'unavailable'),
        'matchNumStr': row['matchNum'],
        'businessDate': row['businessDate'],
        'kickoff': row['kickoff'],
        'league': row.get('league', 'unavailable'),
        'home': row.get('home', 'unavailable'),
        'away': row.get('away', 'unavailable'),
        'matchStatus': row.get('status', 'unavailable'),
        'pools': {
            'had': {'status': 'offered' if row.get('european') and 'status' not in row['european'] else 'unavailable', 'values': row.get('european', {})},
            'hhad': {'status': 'offered' if row.get('handicap') and 'status' not in row['handicap'] else 'unavailable', 'values': row.get('handicap', {})},
            'ttg': {'status': 'offered' if row.get('goals') and 'status' not in row['goals'] else 'unavailable', 'values': row.get('goals', {})},
            'crs': {'status': 'offered' if row.get('exactScores') and 'status' not in row['exactScores'] else 'unavailable', 'values': row.get('exactScores', {})},
        },
    })
out = {'date': args.date, 'businessDate': f'{args.date[:4]}-{args.date[4:6]}-{args.date[6:]}', 'fetchedAt': datetime.now().astimezone().isoformat(timespec='seconds'), 'source': 'data/market_snapshots_' + args.date + '.jsonl', 'matches': matches}
(ROOT / 'data' / f'sporttery_{args.date}.json').write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'date': args.date, 'matches': len(matches)}, ensure_ascii=False))
