# A-Share Hot Sectors Daily

GitHub Actions + Python pipeline that scrapes the daily "A股五张图" article from [xuangutong.com.cn](https://xuangutong.com.cn/subject/777), extracts hot sectors and their leading stocks from the market recap, and commits the result daily.

## How it works

- Runs every trading day at 19:00 Beijing time (UTC 11:00), Mon–Fri; manual runs via `workflow_dispatch`.
- If the article isn't out yet, retries every 10 minutes (up to 5 times).
- Parses the first section (`h2`) of the article: `<strong>` = sector, `/stock/` links = stocks, first 5 per sector in article order.
- Writes `data/YYYY/MM/YYYY-MM-DD.json` and `.md`, then commits as `data: update daily hot sectors`.

## Data

```json
{
  "date": "2026-09-07",
  "source": "https://xuangutong.com.cn/article/1290482",
  "title": "A股五张图：…",
  "sectors": [
    { "sector": "CPO", "stocks": ["剑桥科技", "铭普光磁", "汇绿生态", "光迅科技", "华盛昌"] }
  ]
}
```

## Local run

```bash
pip install -r requirements.txt
python scripts/fetch_article.py --date 2026-09-07 --commit
```

## Failure policy

If anything looks wrong (site unreachable, page structure changed, date mismatch, no sectors found), the run fails and **no data is written** — better a missing day than wrong data.
