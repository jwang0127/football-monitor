import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
date = '20260823'
rows = [json.loads(line) for line in (ROOT / 'data' / f'market_snapshots_{date}.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
groups = defaultdict(list)
for row in rows:
    if row.get('matchNum', '').endswith(('001','002','003','004','005')):
        groups[row['matchNum']].append(row)

def direction(old, new):
    try:
        a, b = float(old), float(new)
    except (TypeError, ValueError):
        return '变动' if old != new else '—'
    return '↑' if b > a else '↓' if b < a else '—'

def explain(label, changed):
    if not changed:
        return f'{label}未变'
    return f'{label}发生{changed}项变化：赔率下降表示该档位隐含概率相对上升，赔率上升表示相对走弱；不等于结果确定'

out = ['# 2026-08-23 17:30 前五场阶段汇总', '', f'生成时间：{rows[-1]["capturedAt"]}；官方纳入场次：27；本汇总范围：001–005。', '', '## 阶段说明', '', '仅描述可验证的时间点变化。比分为 unavailable/not_started 时，不推断为0:0；500或海外字段缺失时保留 unavailable。以下分析仅供娱乐参考，不承诺提高中奖概率。', '']
for match in sorted(groups):
    values = groups[match]
    latest = values[-1]
    out += [f'## {match} {latest.get("home", "unavailable")} vs {latest.get("away", "unavailable")}', '', f'开赛：{latest.get("kickoff", "unavailable")}；当前比分：{latest.get("score", "unavailable")}；状态：{latest.get("status", "unavailable")}；数据点：{len(values)}。', '', '|时段|时间点|亚盘主/客|欧赔主/平/客|进球数赔率|比分赔率档位|本点解读|', '|---|---|---|---|---|---|---|']
    previous = None
    for row in values:
        hour = int(row['capturedAt'][11:13])
        period = '凌晨' if hour < 6 else '上午' if hour < 12 else '下午' if hour < 18 else '晚间'
        handicap, european = row.get('handicap', {}), row.get('european', {})
        goals, exact = row.get('goals', {}), row.get('exactScores', {})
        if previous:
            details = []
            for field, label in [('handicap','亚盘'),('european','欧赔'),('goals','进球数赔率'),('exactScores','比分赔率')]:
                before, after = previous.get(field, {}), row.get(field, {})
                changed = len([k for k in set(before) | set(after) if before.get(k) != after.get(k)])
                if changed:
                    details.append(explain(label, changed))
            reading = '；'.join(details) if details else '本时间点未识别到变化'
        else:
            reading = '首个时间点，建立基准；外部数据缺口按unavailable记录'
        out.append(f'|{period}|{row["capturedAt"]}|{handicap.get("h", "unavailable")} / {handicap.get("a", "unavailable")}|{european.get("h", "unavailable")} / {european.get("d", "unavailable")} / {european.get("a", "unavailable")}|{goals}|{len(exact)}个比分档位|{reading}|')
        previous = row
    out += ['', '数据缺口：500对应日期页面若返回空行，记为unavailable；官方实时比分/进球字段缺失时不做猜测。', '']

target = ROOT / 'output' / 'stage_summary_20260823_1730.md'
target.write_text('\n'.join(out) + '\n', encoding='utf-8')
print(json.dumps({'matches': len(groups), 'output': str(target)}, ensure_ascii=False))
