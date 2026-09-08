# A-Share Hot Sectors Daily

GitHub Actions + Python pipeline that collect information from the market, extracts hot sectors and their leading stocks from the market recap, and commits the result daily.

## Local run

```bash
pip install -r requirements.txt
python scripts/fetch_article.py --date 2026-09-07 --commit
```

## Failure policy

If anything looks wrong (site unreachable, page structure changed, date mismatch, no sectors found), the run fails and **no data is written** — better a missing day than wrong data.
