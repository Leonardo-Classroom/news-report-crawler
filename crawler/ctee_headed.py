"""工商時報專用補齊腳本：用『有頭』瀏覽器（搭配 xvfb 虛擬顯示）突破 Cloudflare WAF，
點「載入更多」取得每位記者的完整文章，逐記者去重存檔。

執行（需先安裝 xvfb）：
  xvfb-run -a conda run -n leo3.10 python ctee_headed.py
"""
import os
import re
import sys
import time
from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (log, save_article, load_json, sanitize, parse_date,
                    DATASET, SITE_NAME, SITE_BASE, UA)

REPORTERS = DATASET / "記者清單.json"
CTEE_ART = re.compile(r"/news/\d{14}-\d+")


def reporter_done_urls(name):
    folder = DATASET / SITE_NAME["ctee"] / sanitize(name, 60)
    done = set()
    if folder.exists():
        for f in folder.glob("*.txt"):
            try:
                with open(f, encoding="utf-8") as fh:
                    for line in fh:
                        if line.startswith("網址："):
                            done.add(line.split("網址：", 1)[1].strip())
                            break
            except Exception:
                pass
    return done


def collect_links(pg, rid, max_clicks=600):
    """初始頁 + 不斷點『載入更多』，回傳 [(url,title)]。"""
    pg.goto(f"{SITE_BASE['ctee']}/reporter/{rid}",
            wait_until="domcontentloaded", timeout=60000)
    pg.wait_for_timeout(2500)
    h1 = pg.query_selector("h1")
    name = (h1.inner_text() or "").strip().split()[-1] if h1 else str(rid)
    stagnant = last = 0
    for _ in range(max_clicks):
        btn = pg.query_selector("button:has-text('載入更多'), a:has-text('載入更多')")
        if not btn or not btn.is_visible():
            break
        try:
            btn.scroll_into_view_if_needed(timeout=4000)
            btn.click(timeout=4000)
        except Exception:
            try:
                pg.evaluate("(b)=>b.click()", btn)
            except Exception:
                break
        pg.wait_for_timeout(1300)
        cur = len(pg.query_selector_all("a[href*='/news/']"))
        if cur == last:
            stagnant += 1
            if stagnant >= 3:
                break
        else:
            stagnant = 0
            last = cur
    seen = {}
    for a in pg.query_selector_all("h3 a, a[href*='/news/']"):
        href = a.get_attribute("href") or ""
        if not CTEE_ART.search(href):
            continue
        url = href if href.startswith("http") else SITE_BASE["ctee"] + href
        url = url.split("?")[0]
        title = (a.inner_text() or "").strip().replace("\n", " ")
        if url and title and url not in seen:
            seen[url] = title
    return name, list(seen.items())


def parse_article(pg):
    h1 = pg.query_selector("h1")
    title = h1.inner_text().strip() if h1 else "untitled"
    el = pg.query_selector('meta[property="article:published_time"]')
    date = parse_date(el.get_attribute("content")) if el else "unknown"
    if date == "unknown":
        t = pg.query_selector("time, .date")
        date = parse_date(t.get_attribute("datetime") or t.inner_text()) if t else "unknown"
    body_el = pg.query_selector("div.article-body") or pg.query_selector("article")
    return title, date, (body_el.inner_text().strip() if body_el else "")


def main():
    reporters = [r for r in load_json(REPORTERS, []) if r["site"] == "ctee"]
    log(f"工商補齊：{len(reporters)} 位記者（有頭瀏覽器）")
    total_new = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False,
                                    args=["--disable-blink-features=AutomationControlled"])
        for idx, r in enumerate(reporters, 1):
            ctx = browser.new_context(user_agent=UA, locale="zh-TW",
                                      viewport={"width": 1366, "height": 900})
            pg = ctx.new_page()
            try:
                name, links = collect_links(pg, r["reporter_id"])
                done = reporter_done_urls(name)
                todo = [(u, t) for u, t in links if u not in done]
                log(f"[{idx}/{len(reporters)}] {name}：連結{len(links)}、待抓{len(todo)}")
                new = 0
                for u, t in todo:
                    try:
                        pg.goto(u, wait_until="domcontentloaded", timeout=45000)
                        pg.wait_for_timeout(800)
                        title, date, body = parse_article(pg)
                        if body:
                            _pth, created = save_article("ctee", name, date,
                                                         title or t, u, body)
                            if created:
                                new += 1
                    except Exception as e:
                        log(f"    ! {u} 失敗：{str(e)[:50]}")
                total_new += new
                log(f"[{idx}/{len(reporters)}] {name} 完成，新增 {new} 篇")
            except Exception as e:
                log(f"[{idx}/{len(reporters)}] {r['name']} 失敗：{str(e)[:60]}")
            finally:
                try:
                    ctx.close()
                except Exception:
                    pass
        browser.close()
    log(f"工商補齊完成，共新增 {total_new} 篇")


if __name__ == "__main__":
    main()
