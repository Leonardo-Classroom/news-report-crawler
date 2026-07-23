"""測試腳本：三站各取一位記者，爬 3 篇文章並存檔，驗證端到端流程。"""
import os
import sys
from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load, log, save_article, make_context, SITE_BASE
import sites

N = 3  # 每位記者測試幾篇


def test_udn(ctx):
    pg = ctx.new_page()
    load(pg, f"{SITE_BASE['udn']}/news/reporter/MDczNzY=")
    name = sites.udn_reporter_name(pg)
    links = sites.udn_article_links(pg, max_scroll=1)[:N]
    log(f"UDN {name}：取 {len(links)} 篇")
    for url, _ in links:
        load(pg, url)
        title, date, body = sites.udn_parse_article(pg)
        path, created = save_article("udn", name, date, title, url, body)
        log(f"  {'新增' if created else '已存在'} | {date} | {title[:24]} | 內文{len(body)}字")
    pg.close()


def test_ct(ctx):
    pg = ctx.new_page()
    links = sites.ct_article_links(pg, "1893", max_pages=1)[:N]
    name = sites.ct_reporter_name(pg)
    log(f"中時 {name}：取 {len(links)} 篇")
    for url, _ in links:
        load(pg, url, cloudflare=True)
        title, date, body = sites.ct_parse_article(pg)
        path, created = save_article("chinatimes", name, date, title, url, body)
        log(f"  {'新增' if created else '已存在'} | {date} | {title[:24]} | 內文{len(body)}字")
    pg.close()


def test_ctee(ctx):
    pg = ctx.new_page()
    links = sites.ctee_article_links(pg, "60117", max_clicks=0)[:N]
    load(pg, f"{SITE_BASE['ctee']}/reporter/60117")
    name = sites.ctee_reporter_name(pg)
    log(f"工商 {name}：取 {len(links)} 篇")
    for url, _ in links:
        load(pg, url)
        title, date, body = sites.ctee_parse_article(pg)
        path, created = save_article("ctee", name, date, title, url, body)
        log(f"  {'新增' if created else '已存在'} | {date} | {title[:24]} | 內文{len(body)}字")
    pg.close()


def main():
    with sync_playwright() as p:
        browser, ctx = make_context(p)
        try:
            log("=== 測試 UDN ===")
            test_udn(ctx)
            log("=== 測試 中時 ===")
            test_ct(ctx)
            log("=== 測試 工商 ===")
            test_ctee(ctx)
        finally:
            browser.close()
    log("測試完成")


if __name__ == "__main__":
    main()
