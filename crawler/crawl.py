"""讀取 dataset/記者清單.json，爬取每位記者的所有文章。

文章存到 dataset/<新聞網>/<記者>/<日期>-<標題>.txt
可續爬：已存在的檔案會跳過；每位記者完成後記錄於 _state/done_reporters.json。

用法：
  python crawl.py            # 爬全部站台
  python crawl.py udn        # 只爬指定站台 (udn/chinatimes/ctee)
"""
import os
import sys
from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (load, log, save_json, load_json, save_article, DATASET,
                    STATE, make_context, SITE_NAME)
import sites

REPORTERS = DATASET / "記者清單.json"
DONE_FILE = STATE / "done_reporters.json"

CFG = {
    "udn":        dict(cloudflare=False),
    "chinatimes": dict(cloudflare=True),
    "ctee":       dict(cloudflare=False),
}


def get_links(pg, r):
    site, rid, url = r["site"], r["reporter_id"], r["url"]
    if site == "udn":
        load(pg, url)
        return sites.udn_article_links(pg)
    if site == "chinatimes":
        return sites.ct_article_links(pg, rid)
    if site == "ctee":
        return sites.ctee_article_links(pg, rid)
    return []


def parse(pg, site):
    if site == "udn":
        return sites.udn_parse_article(pg)
    if site == "chinatimes":
        return sites.ct_parse_article(pg)
    if site == "ctee":
        return sites.ctee_parse_article(pg)


def crawl_reporter(ctx, r):
    site, name = r["site"], r["name"]
    cloudflare = CFG[site]["cloudflare"]
    pg = ctx.new_page()
    log(f"  {SITE_NAME[site]}/{name}：收集文章連結中…")
    try:
        links = get_links(pg, r)
    except Exception as e:
        log(f"  ! 取得 {name} 文章列表失敗：{e}")
        pg.close()
        return 0, 0
    log(f"  {SITE_NAME[site]}/{name}：找到 {len(links)} 篇文章")
    new = skip = 0
    for url, _title in links:
        try:
            load(pg, url, cloudflare=cloudflare)
            title, date, body = parse(pg, site)
            if not body:
                continue
            _path, created = save_article(site, name, date, title, url, body)
            if created:
                new += 1
            else:
                skip += 1
        except Exception as e:
            log(f"    ! 文章失敗 {url}：{e}")
    pg.close()
    log(f"  {name}：新增 {new}、已存在 {skip}")
    return new, skip


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else "all"
    reporters = load_json(REPORTERS, [])
    if only != "all":
        reporters = [r for r in reporters if r["site"] == only]
    done = set(load_json(DONE_FILE, []))
    log(f"共 {len(reporters)} 位記者待處理（已完成 {len(done)} 位略過）")

    total_new = 0
    with sync_playwright() as p:
        browser, ctx = make_context(p)
        try:
            for i, r in enumerate(reporters, 1):
                key = f"{r['site']}:{r['reporter_id']}"
                if key in done:
                    continue
                log(f"[{i}/{len(reporters)}] {SITE_NAME[r['site']]} - {r['name']}")
                new, _ = crawl_reporter(ctx, r)
                total_new += new
                done.add(key)
                save_json(DONE_FILE, sorted(done))
        finally:
            browser.close()
    log(f"全部完成：本次新增 {total_new} 篇文章")


if __name__ == "__main__":
    main()
