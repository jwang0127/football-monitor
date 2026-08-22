import argparse, json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument('--date', default='20260822')
args = parser.parse_args()
path = ROOT / 'data' / f'market_snapshots_{args.date}.jsonl'
rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
groups = defaultdict(list)
for row in rows:
    groups[row.get('matchNum', 'unavailable')].append(row)
latest = {key: values[-1] for key, values in groups.items()}

def changed_fields(values):
    fields = []
    for field, label in [('handicap','亚盘'),('european','欧赔'),('goals','进球数'),('exactScores','比分赔率'),('score','比分')]:
        if values[0].get(field) != values[-1].get(field):
            fields.append(label)
    return fields

def score_direction(values):
    up = down = 0
    keys = set().union(*(set(row.get('exactScores', {})) for row in values))
    for key in keys:
        first = values[0].get('exactScores', {}).get(key)
        last = values[-1].get('exactScores', {}).get(key)
        if isinstance(first, (int, float)) and isinstance(last, (int, float)) and first != last:
            if last > first: up += 1
            else: down += 1
    return up, down

def status(row, field):
    value = row.get(field)
    return 'unavailable' if not value or value == {'status':'unavailable'} else 'available'

out = [f'# 2026-08-22 足球雷达21:00汇总', '', f'生成时间：{datetime.now().astimezone().isoformat(timespec="seconds")}；累计场次：{len(latest)}；累计时间点：{len(rows)}。', '', '## 总体分组', '']
groups_text = defaultdict(list)
for key, values in groups.items():
    fields = changed_fields(values)
    up, down = score_direction(values)
    if fields:
        groups_text['有变动'].append(f'{key}（{"、".join(fields)}；比分赔率{up}↑/{down}↓）')
    else:
        groups_text['无官方变动'].append(key)
for label in ('有变动','无官方变动'):
    out.append(f'- **{label}**：' + ('、'.join(sorted(groups_text[label])) if groups_text[label] else '无'))
out += ['', '## 逐场汇总', '', '|编号|主队|客队|开赛|时间点数|初→末变化|比分赔率方向|数据缺口|解读|', '|---|---|---|---|---:|---|---|---|---|']
for key in sorted(latest):
    values = groups[key]
    last = values[-1]
    fields = changed_fields(values)
    up, down = score_direction(values)
    gaps = [label for field, label in [('handicap','亚盘'),('european','欧赔'),('goals','进球数'),('exactScores','比分赔率')] if status(last, field) == 'unavailable']
    reading = '；'.join(fields) + '发生重估，需结合方向与公司分布' if fields else '官方时间序列未识别到变化；不等于外盘没有变化'
    out.append(f'|{key}|{last.get("home","unavailable")}|{last.get("away","unavailable")}|{last.get("kickoff","unavailable")[11:16]}|{len(values)}|{"、".join(fields) if fields else "无"}|{up}↑/{down}↓|{"、".join(gaps) if gaps else "无"}|{reading}|')
out += ['', '## 异常与缺失', '', '- 001–008等已不在后续官方返回列表的场次，只保留历史快照；不将其从历史数据中删除，也不补造新盘口。', '- 比分字段若为 unavailable，表示当前采集源未返回可验证实时比分，不代表0:0。', '- 盘口赔率变动仅是市场信息整理，不构成提高中奖概率的承诺或购彩建议。']
target = ROOT / 'output' / f'market_summary_{args.date}_2100.md'
target.write_text('\n'.join(out) + '\n', encoding='utf-8')
print(json.dumps({'matches': len(latest), 'snapshots': len(rows), 'output': str(target)}, ensure_ascii=False))
