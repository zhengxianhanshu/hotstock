# A股热门板块每日自动统计系统

基于 GitHub Actions + Python 的自动化程序。每个交易日收盘后自动抓取[选股通《A股五张图》](https://xuangutong.com.cn/subject/777)专栏当天文章，提取市场综述中的热门板块及对应股票（每板块按文章出现顺序取前 5 只），生成结构化数据并提交到仓库，形成长期的 A 股热门题材历史数据库。

## 数据来源

- 专栏列表页：`https://xuangutong.com.cn/subject/777`（定位当天文章）
- 文章页：`https://xuangutong.com.cn/article/<id>`（正文、发布日期）

识别规则（依赖页面结构，非自然语言猜测）：

- 市场综述 = 正文第一个 `h2` 小节（"1、行情"）至第二个 `h2` 之间的内容；
- 热门板块 = 综述中的 `<strong>` 文本（如 农林牧渔、CPO、PCB板块、CPU概念）；
- 股票 = 综述中 `a[href*="/stock/"]` 红色超链接，按出现顺序每板块最多 5 只；
- 只提及板块但未列个股的（如"AI硬件产业链、机器人、旅游"），跳过。

## 运行

```bash
pip install -r requirements.txt
python scripts/fetch_article.py --date 2026-09-07          # 指定日期
python scripts/fetch_article.py --commit                   # 成功后 git commit + push
```

参数：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--date` | 北京时间今天 | 目标日期 `YYYY-MM-DD` |
| `--retries` | 5 | 当天文章检查次数（19:00 起每 10 分钟一次至 19:40） |
| `--interval-min` | 10 | 检查间隔（分钟） |
| `--data-dir` | `data` | 输出目录 |
| `--commit` | 关 | 生成成功后 `git add/commit/push` |

## 自动化

`.github/workflows/daily.yml`：北京时间每天 19:00（UTC 11:00，周一至五）定时运行，支持 `workflow_dispatch` 手动触发。文章晚于 19:00 发布时按 10 分钟间隔轮询重试；同一 workflow 串行执行（`concurrency`），避免并发重复提交。

## 输出

- `data/YYYY/MM/YYYY-MM-DD.json` — 结构化数据（date / source / title / sectors）
- `data/YYYY/MM/YYYY-MM-DD.md` — 人工阅读版

示例见 [`data/2026/09/2026-09-07.json`](data/2026/09/2026-09-07.json)。

Commit 规则：仅在有新数据时提交，消息为 `data: update daily hot sectors`，不做空提交。

## 防错原则

- 已有当天 JSON 则跳过，不重复生成；
- 网站访问失败自动重试；超过最大重试未找到当天文章则本次失败；
- 页面结构变化、文章日期不符、解析不出板块时直接失败退出，**宁可当天没有数据，也不生成错误数据**。
