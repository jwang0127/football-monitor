# 足球雷达

这是从 `yuanbao_python_20260820_ZhZRAn.py` 的伪代码落地而成的可审计足球赛前信息与预测流水线。

核心原则：

- 赛程和竞彩玩法只认中国竞彩网实时接口，并按 `businessDate` 分组。
- 外部赛程、近期完赛记录、伤停、交锋、积分和公司赔率由 API-Football 逐场核验。
- “接口已核验但返回 0 条记录”与“没有执行采集”严格区分。
- 不使用空字符串、`None`、虚构球员、虚构赔率或缺失时的中性默认分。
- 任何必需采集步骤失败，该场不进入可发布预测。

## 运行

PowerShell：

```powershell
python .\yuanbao_python_20260820_ZhZRAn.py run --date 20260820 --refresh
python .\yuanbao_python_20260820_ZhZRAn.py audit --date 20260820
python -m unittest discover -s tests -v
```

需要环境变量 `API_FOOTBALL_KEY`。脚本兼容被单引号或双引号包裹的 key。

输出位于：

- `data/sporttery_YYYYMMDD.json`：官方竞彩规范化快照
- `data/evidence_YYYYMMDD.json`：逐场证据摘要
- `output/prediction_YYYYMMDD.json`：机器可读预测及完整度审计
- `output/prediction_YYYYMMDD.md`：中文完整报告
- `YYYYMMDD/index.html`：静态网页

“100%”指本项目定义的必需采集检查全部执行成功、每个结论都有来源，不代表比赛结果命中率。足球比赛存在随机性，任何模型都不能保证预测命中。
