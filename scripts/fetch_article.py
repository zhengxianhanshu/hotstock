#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A股五张图 · 每日热门板块统计。

流程：
  1. 轮询选股通《A股五张图》专栏页，定位目标日期的文章；
  2. 抓取文章页，解析第一个小节（市场综述）；
  3. 综述中 <strong> 为板块、a[href*="/stock/"] 为个股，按文章出现顺序每板块取前 5 只；
  4. 生成 data/YYYY/MM/YYYY-MM-DD.json 与同名 .md；
  5. 可选 --commit：git add / commit / push。

失败语义（需求 §13）：任何一步失败都不写数据文件，退出码非 0。
"""

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BEIJING_TZ = timezone(timedelta(hours=8))
SITE = "https://xuangutong.com.cn"
SUBJECT_URL = SITE + "/subject/777"  # 《A股五张图》专栏
ARTICLE_URL = SITE + "/article/{aid}"
MAX_STOCKS_PER_SECTOR = 5
COMMIT_MESSAGE = "data: update daily hot sectors"
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
}
HTTP_TIMEOUT = 30

SECTOR_SUFFIX_RE = re.compile(r"(板块|概念股|概念)$")
SECTOR_VERB_RE = re.compile(r"涨停|跌停|连板|上涨|下跌|涨幅|跌幅|收涨|收跌")
STOCK_HREF_RE = re.compile(r"/stock/([0-9A-Za-z_.]+)")


class FetchError(RuntimeError):
    """可重试错误：网络访问失败、当天文章尚未发布。"""


class FatalError(RuntimeError):
    """不可重试错误：页面结构变化、日期不一致、git 失败等。"""


def log(msg):
    print(f"[{datetime.now(BEIJING_TZ):%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def http_get(url, attempts=3):
    last = None
    for i in range(attempts):
        try:
            resp = requests.get(url, headers=HTTP_HEADERS, timeout=HTTP_TIMEOUT)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            last = exc
            log(f"GET {url} 失败（第 {i + 1}/{attempts} 次）：{exc}")
            if i < attempts - 1:
                time.sleep(2 * (i + 1))
    raise FetchError(f"网站访问失败：{url}（{last}）")


def find_article_for_date(subject_html, target_date):
    """在专栏列表页中定位目标日期（YYYY-MM-DD）的文章。列表按时间倒序。"""
    soup = BeautifulSoup(subject_html, "html.parser")
    items = soup.select("#article-list li.article-list-item")
    if not items:
        raise FatalError("解析失败：专栏页未找到 #article-list 列表项（页面结构可能已变化）")
    for li in items:
        a = li.select_one("a[href^='/article/']")
        t = li.select_one("time")
        if a is None or t is None:
            continue
        m = re.search(r"(\d{4}/\d{2}/\d{2})", t.get_text())
        if not m:
            continue
        item_date = datetime.strptime(m.group(1), "%Y/%m/%d").strftime("%Y-%m-%d")
        if item_date == target_date:
            aid = a["href"].rsplit("/article/", 1)[-1]
            if not re.fullmatch(r"\d+", aid):
                raise FatalError(f"解析失败：非法文章 ID「{aid}」")
            return aid, a.get_text(strip=True)
        if item_date < target_date:
            break  # 倒序列表已越过目标日期 => 当天文章尚未发布
    return None, None


def clean_sector_name(raw):
    name = re.sub(r"[\s\u00a0]+", "", raw or "")
    name = SECTOR_SUFFIX_RE.sub("", name)
    if not name or len(name) > 30:
        return None
    if re.match(r"^\d", name):  # 例：20CM跌停
        return None
    if SECTOR_VERB_RE.search(name):  # 涨跌描述而非板块名，例：超3100股上涨
        return None
    return name


def parse_publish_date(soup):
    node = soup.select_one(".meta_1PmwL time") or soup.find("time")
    if node:
        m = re.search(r"(\d{4}/\d{2}/\d{2})", node.get_text())
        if m:
            return datetime.strptime(m.group(1), "%Y/%m/%d").strftime("%Y-%m-%d")
    raise FatalError("解析失败：文章页未找到发布时间")


def parse_article(article_html, target_date):
    soup = BeautifulSoup(article_html, "html.parser")
    title_node = soup.select_one("h1")
    title = title_node.get_text(strip=True) if title_node else ""
    if not title:
        raise FatalError("解析失败：文章页未找到标题 h1")

    publish_date = parse_publish_date(soup)
    if publish_date != target_date:
        raise FatalError(f"文章日期校验失败：期望 {target_date}，实际 {publish_date}")

    content = soup.select_one(".article-content") or soup.select_one("article")
    if content is None:
        raise FatalError("解析失败：文章页未找到正文容器 .article-content")

    h2s = content.find_all("h2")
    if not h2s:
        raise FatalError("解析失败：正文未找到任何 h2 小节标题")

    # 市场综述 = 第一个 h2 到第二个 h2 之间的内容
    section_nodes = []
    for el in h2s[0].find_next_siblings():
        if el.name == "h2":
            break
        section_nodes.append(el)
    if not section_nodes:
        raise FatalError("解析失败：第一个 h2 小节（市场综述）内没有内容节点")

    sectors = []  # [name, [stocks...]]，保持文档顺序
    current = None
    for node in [n for el in section_nodes for n in el.find_all(["strong", "a"])]:
        if node.name == "strong":
            name = clean_sector_name(node.get_text())
            if name is None:
                continue
            current = [name, []]
            sectors.append(current)
        else:
            href = node.get("href") or ""
            m = STOCK_HREF_RE.search(href)
            if m and current is not None:
                stock = re.sub(r"\s+", "", node.get_text())
                if stock and stock not in current[1]:
                    current[1].append(stock)

    result = []
    skipped = []
    for name, stocks in sectors:
        if not stocks:
            skipped.append(name)
            continue
        result.append({"sector": name, "stocks": stocks[:MAX_STOCKS_PER_SECTOR]})
    if skipped:
        log(f"提示：以下板块提及但综述未列出个股，跳过：{'、'.join(skipped)}")
    if not result:
        raise FatalError("解析失败：市场综述中未能识别出任何带个股的热门板块")
    return {"title": title, "date": target_date, "sectors": result}


def build_markdown(data, source_url):
    lines = [
        f"# A股五张图 · {data['date']}",
        "",
        f"来源：[A股五张图]({source_url})",
        "",
        "## 热门板块",
        "",
    ]
    for sec in data["sectors"]:
        lines.append(f"### {sec['sector']}")
        lines.append("")
        for i, stock in enumerate(sec["stocks"], 1):
            lines.append(f"{i}. {stock}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _write_atomic(path, text):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def write_outputs(data_dir, target, data, source_url):
    base = Path(data_dir) / target[:4] / target[5:7]
    base.mkdir(parents=True, exist_ok=True)
    json_path = base / f"{target}.json"
    md_path = base / f"{target}.md"
    payload = {
        "date": data["date"],
        "source": source_url,
        "title": data["title"],
        "sectors": data["sectors"],
    }
    _write_atomic(json_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    _write_atomic(md_path, build_markdown(data, source_url))
    return [json_path, md_path]


def commit_and_push(files):
    try:
        inside = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True,
        )
        if inside.returncode != 0:
            raise FatalError("--commit 需要在 git 仓库内运行")
        subprocess.run(["git", "add", "--", *[str(f) for f in files]], check=True)
        if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode == 0:
            log("没有可提交的变更，跳过 commit。")
            return
        subprocess.run(["git", "commit", "-m", COMMIT_MESSAGE], check=True)
        subprocess.run(["git", "push"], check=True)
        log(f"已 commit 并 push：{COMMIT_MESSAGE}")
    except subprocess.CalledProcessError as exc:
        raise FatalError(f"git 操作失败：{exc}") from exc


def parse_args(argv):
    parser = argparse.ArgumentParser(description="抓取《A股五张图》综述并生成当日热门板块数据")
    parser.add_argument("--date", help="目标日期 YYYY-MM-DD（默认：北京时间今天）")
    parser.add_argument("--data-dir", default="data", help="输出目录（默认 data）")
    parser.add_argument("--retries", type=int, default=5, help="当天文章检查次数（默认 5，对应 19:00-19:40）")
    parser.add_argument("--interval-min", type=int, default=10, help="检查间隔分钟数（默认 10）")
    parser.add_argument("--commit", action="store_true", help="生成成功后 git commit 并 push")
    return parser.parse_args(argv)


def main(argv=None):
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args(argv)
    target = args.date or datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
    try:
        datetime.strptime(target, "%Y-%m-%d")
    except ValueError:
        log(f"参数错误：--date 需为 YYYY-MM-DD，收到「{target}」")
        return 2

    json_path = Path(args.data_dir) / target[:4] / target[5:7] / f"{target}.json"
    if json_path.exists():
        log(f"当天数据已存在：{json_path}，跳过（防重复）。")
        return 0

    article_url = None
    for attempt in range(1, args.retries + 1):
        try:
            subject_html = http_get(SUBJECT_URL)
            aid, list_title = find_article_for_date(subject_html, target)
            if aid is None:
                log(f"第 {attempt}/{args.retries} 次检查：{target} 的《A股五张图》尚未发布。")
            else:
                article_url = ARTICLE_URL.format(aid=aid)
                log(f"找到当天文章：{article_url}（{list_title}）")
                data = parse_article(http_get(article_url), target)
                files = write_outputs(args.data_dir, target, data, article_url)
                for f in files:
                    log(f"已生成 {f}")
                if args.commit:
                    commit_and_push(files)
                log(f"完成：{len(data['sectors'])} 个板块，"
                    f"{sum(len(s['stocks']) for s in data['sectors'])} 只个股。")
                return 0
        except FatalError as exc:
            log(f"致命错误：{exc}")
            return 1
        except FetchError as exc:
            log(f"错误：{exc}")
        if attempt < args.retries:
            log(f"等待 {args.interval_min} 分钟后重试…")
            time.sleep(args.interval_min * 60)

    log(f"失败：超过最大重试次数，仍未获得 {target} 的有效数据（未生成任何文件）。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
