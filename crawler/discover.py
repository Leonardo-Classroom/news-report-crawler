"""發現三個新聞網的記者，輸出 dataset/記者清單.json。

用法：
  python discover.py                 # 預設範圍
  CT_PAGES=10 CTEE_CLICKS=30 python discover.py
可重複執行，會與既有清單合併。
"""
import os
import sys
from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (load, log, save_json, load_json, DATASET, SITE_NAME,
                    SITE_BASE, make_context, is_generic_byline)
import sites

OUT = DATASET / "記者清單.json"

CT_PAGES = int(os.environ.get("CT_PAGES", "10"))        # 中時即時新聞掃描頁數
CTEE_CLICKS = int(os.environ.get("CTEE_CLICKS", "30"))  # 工商「載入更多」次數
UDN_SCROLLS = int(os.environ.get("UDN_SCROLLS", "20"))  # UDN 即時新聞滾動次數
UDN_SEED = os.environ.get("UDN_SEED", "MDczNzY=")
CT_SEED = os.environ.get("CT_SEED", "1893")
CTEE_SEED = os.environ.get("CTEE_SEED", "60117")


def discover_udn(ctx, extra_seeds=(), hops=3):
    """UDN：從種子記者頁的『熱門記者』取得清單，多層 BFS 擴展（每層造訪所有新發現的記者頁）。"""
    pg = ctx.new_page()
    result = {}
    seeds = [UDN_SEED, *extra_seeds]
    frontier = set()
    for seed in seeds:
        load(pg, f"{SITE_BASE['udn']}/news/reporter/{seed}")
        for c, n in sites.udn_hot_reporters(pg).items():
            result.setdefault(c, n)
            frontier.add(c)
    log(f"  UDN 種子頁取得 {len(result)} 位熱門記者")
    visited = set(seeds)
    for hop in range(hops):
        frontier -= visited
        if not frontier:
            break
        next_frontier = set()
        for code in sorted(frontier):
            visited.add(code)
            load(pg, f"{SITE_BASE['udn']}/news/reporter/{code}")
            for c, n in sites.udn_hot_reporters(pg).items():
                if c not in result:
                    next_frontier.add(c)
                result.setdefault(c, n)
        log(f"  UDN 第 {hop + 1} 層擴展後共 {len(result)} 位記者")
        frontier = next_frontier
    # 額外：掃描即時新聞、逐篇造訪取得作者（涵蓋熱門記者小工具以外的人）
    for c, n in sites.udn_collect_authors_from_feed(pg, UDN_SCROLLS).items():
        result.setdefault(c, n)
    pg.close()
    log(f"  UDN 共發現 {len(result)} 位記者")
    return [{"name": n, "site": "udn", "site_name": SITE_NAME["udn"],
             "reporter_id": c,
             "url": f"{SITE_BASE['udn']}/news/reporter/{c}"}
            for c, n in result.items()]


def discover_ct(ctx):
    pg = ctx.new_page()
    found = sites.ct_collect_authors_from_feed(pg, range(1, CT_PAGES + 1))
    # 確保種子記者在內
    if CT_SEED not in found:
        load(pg, f"{SITE_BASE['chinatimes']}/reporter/{CT_SEED}?chdtv",
             cloudflare=True)
        found[CT_SEED] = sites.ct_reporter_name(pg)
    pg.close()
    log(f"  中時共發現 {len(found)} 位記者")
    return [{"name": n, "site": "chinatimes",
             "site_name": SITE_NAME["chinatimes"], "reporter_id": rid,
             "url": f"{SITE_BASE['chinatimes']}/reporter/{rid}"}
            for rid, n in found.items()]


def discover_ctee(ctx):
    pg = ctx.new_page()
    found = sites.ctee_collect_authors_from_feed(pg, CTEE_CLICKS)
    if CTEE_SEED not in found:
        load(pg, f"{SITE_BASE['ctee']}/reporter/{CTEE_SEED}")
        found[CTEE_SEED] = sites.ctee_reporter_name(pg)
    pg.close()
    log(f"  工商共發現 {len(found)} 位記者")
    return [{"name": n, "site": "ctee", "site_name": SITE_NAME["ctee"],
             "reporter_id": rid,
             "url": f"{SITE_BASE['ctee']}/reporter/{rid}"}
            for rid, n in found.items()]


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else "all"
    existing = load_json(OUT, [])
    by_key = {(r["site"], r["reporter_id"]): r for r in existing}

    with sync_playwright() as p:
        browser, ctx = make_context(p)
        try:
            if only in ("all", "udn"):
                log("=== 發現 UDN 記者 ===")
                existing_udn = [r["reporter_id"] for r in existing if r["site"] == "udn"]
                for r in discover_udn(ctx, extra_seeds=existing_udn):
                    by_key[(r["site"], r["reporter_id"])] = r
                save_json(OUT, list(by_key.values()))
            if only in ("all", "chinatimes"):
                log("=== 發現中時記者 ===")
                for r in discover_ct(ctx):
                    by_key[(r["site"], r["reporter_id"])] = r
                save_json(OUT, list(by_key.values()))
            if only in ("all", "ctee"):
                log("=== 發現工商記者 ===")
                for r in discover_ctee(ctx):
                    by_key[(r["site"], r["reporter_id"])] = r
                save_json(OUT, list(by_key.values()))
        finally:
            browser.close()

    final = [r for r in by_key.values() if not is_generic_byline(r["name"])]
    save_json(OUT, final)
    counts = {}
    for r in final:
        counts[r["site_name"]] = counts.get(r["site_name"], 0) + 1
    log(f"完成：共 {len(final)} 位記者 -> {OUT}")
    log(f"分佈：{counts}")


if __name__ == "__main__":
    main()
