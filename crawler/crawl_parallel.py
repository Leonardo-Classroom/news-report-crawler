"""並行爬蟲（生產者-消費者）。

- N 個「連結收集」工作緒：各自處理不同記者，邊收集連結邊丟進佇列。
- M 個「文章爬取」工作緒：從佇列取出文章連結，下載並存檔。
連結一收集到就立即交給爬文緒處理，不必等整位記者收完。

用法：
  python crawl_parallel.py                 # 全部站台，10 收集 + 5 爬文
  COLLECTORS=10 CRAWLERS=5 python crawl_parallel.py udn
可續爬：已存在的檔案會跳過；完成收集的記者記於 _state/collected.json。
"""
import os
import re
import sys
import json
import asyncio
from playwright.async_api import async_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (log, save_article, load_json, save_json, DATASET, STATE,
                    SITE_NAME, SITE_BASE, UA, parse_date)
from sites import CT_ART, CTEE_ART

REPORTERS = DATASET / "記者清單.json"
COLLECTED = STATE / "collected.json"
DONE_URLS = STATE / "done_urls.json"

COLLECTORS = int(os.environ.get("COLLECTORS", "10"))
CRAWLERS = int(os.environ.get("CRAWLERS", "5"))


def build_done_urls():
    """掃描既有 .txt 檔，從『網址：』欄位重建已完成 URL 集合（供續爬跳過）。"""
    done = set(load_json(DONE_URLS, []))
    for txt in DATASET.rglob("*.txt"):
        try:
            with open(txt, encoding="utf-8") as f:
                for line in f:
                    if line.startswith("網址："):
                        done.add(line.split("網址：", 1)[1].strip())
                        break
        except Exception:
            pass
    return done


def interleave(reporters):
    """依站台輪流交錯排列，避免單一站台霸佔所有收集緒。"""
    buckets = {}
    for r in reporters:
        buckets.setdefault(r["site"], []).append(r)
    out = []
    while any(buckets.values()):
        for site in list(buckets):
            if buckets[site]:
                out.append(buckets[site].pop(0))
    return out


def _abs(base, href):
    if not href:
        return None
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return base + href
    return href


async def load(pg, url, cloudflare=False, timeout=45000, settle=1200):
    try:
        await pg.goto(url, wait_until="commit" if cloudflare else "domcontentloaded",
                      timeout=timeout)
    except Exception:
        pass
    if cloudflare:
        for _ in range(35):
            await pg.wait_for_timeout(1000)
            t = await pg.title()
            if t and "moment" not in t.lower() and "請稍候" not in t:
                break
    else:
        await pg.wait_for_timeout(settle)


async def text(el):
    return (await el.inner_text()).strip() if el else ""


# ----------------------- 連結收集（每站一種） -----------------------

async def collect_udn(pg, r, push):
    await load(pg, r["url"])
    h1 = await pg.query_selector("h1")
    name = (await text(h1)) or r["name"]
    seen = set()
    stagnant = last = 0
    for i in range(200):
        for a in await pg.query_selector_all(
                "div.context-box__content a[href*='/news/story/']"):
            href = (await a.get_attribute("href") or "").split("?")[0]
            title = (await a.inner_text() or "").strip().replace("\n", " ")
            url = _abs(SITE_BASE["udn"], href)
            if url and title and url not in seen:
                seen.add(url)
                await push("udn", name, url, title, False)
        await pg.mouse.wheel(0, 6000)
        await pg.wait_for_timeout(1300)
        if len(seen) == last:
            stagnant += 1
            if stagnant >= 4:
                break
        else:
            stagnant = 0
            last = len(seen)
    return name, len(seen)


async def collect_ct(pg, r, push):
    base = f"{SITE_BASE['chinatimes']}/reporter/{r['reporter_id']}"
    await load(pg, base + "?chdtv", cloudflare=True)
    name = (await pg.title() or "").split(" - ")[0].strip() or r["name"]
    last = 1
    for a in await pg.query_selector_all(".pagination a"):
        m = re.search(r"page=(\d+)", await a.get_attribute("href") or "")
        if m:
            last = max(last, int(m.group(1)))
    seen = set()
    for page in range(1, last + 1):
        if page > 1:
            await load(pg, f"{base}?page={page}", cloudflare=True)
        for a in await pg.query_selector_all("section.article-list h3 a, h3 a"):
            href = await a.get_attribute("href") or ""
            if not CT_ART.search(href):
                continue
            url = _abs(SITE_BASE["chinatimes"], href.split("?")[0])
            title = (await a.inner_text() or "").strip().replace("\n", " ")
            if url and title and url not in seen:
                seen.add(url)
                await push("chinatimes", name, url, title, True)
    return name, len(seen)


async def collect_ctee(pg, r, push):
    await load(pg, f"{SITE_BASE['ctee']}/reporter/{r['reporter_id']}")
    h1 = await pg.query_selector("h1")
    name = (await text(h1)).split()[-1] if h1 else r["name"]
    for _ in range(400):
        btn = await pg.query_selector(
            "button:has-text('載入更多'), a:has-text('載入更多')")
        if not btn or not await btn.is_visible():
            break
        try:
            await btn.click()
        except Exception:
            break
        await pg.wait_for_timeout(1300)
    seen = set()
    for a in await pg.query_selector_all("h3 a, a[href*='/news/']"):
        href = await a.get_attribute("href") or ""
        if not CTEE_ART.search(href):
            continue
        url = _abs(SITE_BASE["ctee"], href.split("?")[0])
        title = (await a.inner_text() or "").strip().replace("\n", " ")
        if url and title and url not in seen:
            seen.add(url)
            await push("ctee", name, url, title, False)
    return name, len(seen)


COLLECT = {"udn": collect_udn, "chinatimes": collect_ct, "ctee": collect_ctee}


# ----------------------- 文章解析（每站一種） -----------------------

async def parse_udn(pg):
    h1 = await pg.query_selector("h1")
    el = await pg.query_selector('meta[property="article:published_time"]')
    date = parse_date(await el.get_attribute("content")) if el else "unknown"
    if date == "unknown":
        t = await pg.query_selector(".article-content__time")
        date = parse_date(await t.inner_text()) if t else "unknown"
    body = await pg.query_selector("section.article-content__editor")
    return (await text(h1)) or "untitled", date, (await text(body))


async def parse_ct(pg):
    h1 = await pg.query_selector("h1")
    el = await pg.query_selector('meta[property="article:published_time"]')
    date = parse_date(await el.get_attribute("content")) if el else "unknown"
    if date == "unknown":
        t = await pg.query_selector("time")
        date = parse_date(await t.get_attribute("datetime") or await t.inner_text()) if t else "unknown"
    body = await pg.query_selector("div.article-body")
    return (await text(h1)) or "untitled", date, (await text(body))


async def parse_ctee(pg):
    h1 = await pg.query_selector("h1")
    el = await pg.query_selector('meta[property="article:published_time"]')
    date = parse_date(await el.get_attribute("content")) if el else "unknown"
    if date == "unknown":
        t = await pg.query_selector("time, .date")
        date = parse_date(await t.get_attribute("datetime") or await t.inner_text()) if t else "unknown"
    body = (await pg.query_selector("div.article-body")
            or await pg.query_selector("article"))
    return (await text(h1)) or "untitled", date, (await text(body))


PARSE = {"udn": parse_udn, "chinatimes": parse_ct, "ctee": parse_ctee}


# ----------------------- 工作緒 -----------------------

class Stats:
    def __init__(self):
        self.new = 0
        self.skip = 0
        self.lock = asyncio.Lock()


async def collector(idx, ctx, rq, aq, collected, done_urls):
    pg = await ctx.new_page()

    async def push(site, name, url, title, cf):
        if url not in done_urls:                # 已完成的不再入列
            await aq.put((site, name, url, title, cf))

    while True:
        try:
            r = rq.get_nowait()
        except asyncio.QueueEmpty:
            break
        key = f"{r['site']}:{r['reporter_id']}"
        if key in collected:
            rq.task_done()
            continue
        try:
            # 單一記者連結收集最多 20 分鐘（多產記者滾動較久），逾時則重建頁面
            name, n = await asyncio.wait_for(
                COLLECT[r["site"]](pg, r, push), timeout=1200)
            log(f"[收集{idx}] {SITE_NAME[r['site']]}/{name}：{n} 篇連結已入列")
            collected.add(key)
            save_json(COLLECTED, sorted(collected))
        except asyncio.TimeoutError:
            log(f"[收集{idx}] ⏱ {r['site']}/{r['name']} 收集逾時，跳過")
        except Exception as e:
            log(f"[收集{idx}] ! {r['site']}/{r['name']} 失敗：{str(e)[:80]}")
        finally:
            rq.task_done()
    try:
        await pg.close()
    except Exception:
        pass


async def _crawl_one(pg, site, name, url, title, done_urls, stats):
    await load(pg, url, cloudflare=cf_flag(site))
    t, date, body = await PARSE[site](pg)
    if body:
        _p, created = save_article(site, name, date, t or title, url, body)
        done_urls.add(url)
        async with stats.lock:
            if created:
                stats.new += 1
            else:
                stats.skip += 1
        if created and stats.new % 25 == 0:
            log(f"[爬文] 已存 {stats.new} 篇")
            if stats.new % 200 == 0:
                save_json(DONE_URLS, sorted(done_urls))


def cf_flag(site):
    return site == "chinatimes"


async def crawler(idx, ctx, aq, stats, done_urls):
    pg = await ctx.new_page()
    while True:
        try:
            site, name, url, title, cf = await aq.get()
        except asyncio.CancelledError:
            break
        try:
            if url in done_urls:
                async with stats.lock:
                    stats.skip += 1
                continue
            # 硬性逾時：單篇文章最多 90 秒，卡住就重建頁面，避免死結
            await asyncio.wait_for(
                _crawl_one(pg, site, name, url, title, done_urls, stats),
                timeout=90)
        except asyncio.TimeoutError:
            # 不關閉/重建頁面（那會在瀏覽器無回應時卡死）；沿用頁面跳到下一篇
            log(f"[爬文{idx}] ⏱ 逾時跳過：{url}")
        except Exception as e:
            log(f"[爬文{idx}] ! {url} 失敗：{str(e)[:60]}")
        finally:
            aq.task_done()


async def main():
    only = sys.argv[1] if len(sys.argv) > 1 else "all"
    reporters = load_json(REPORTERS, [])
    if only != "all":
        reporters = [r for r in reporters if r["site"] == only]
    collected = set(load_json(COLLECTED, []))
    done_urls = build_done_urls()
    reporters = interleave(reporters)
    log(f"並行爬蟲啟動：{len(reporters)} 位記者，{COLLECTORS} 收集 + {CRAWLERS} 爬文")
    log(f"已完成 {len(done_urls)} 篇（續爬將跳過），已收集記者 {len(collected)} 位")

    rq = asyncio.Queue()
    aq = asyncio.Queue(maxsize=1500)
    for r in reporters:
        rq.put_nowait(r)
    stats = Stats()

    async with async_playwright() as p:
        browser = await p.chromium.launch()

        async def mkctx():
            c = await browser.new_context(user_agent=UA, locale="zh-TW",
                                          viewport={"width": 1366, "height": 900})
            await c.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
            return c

        col_ctxs = [await mkctx() for _ in range(COLLECTORS)]
        crw_ctxs = [await mkctx() for _ in range(CRAWLERS)]

        collectors = [asyncio.create_task(
            collector(i + 1, col_ctxs[i], rq, aq, collected, done_urls))
            for i in range(COLLECTORS)]
        crawlers = [asyncio.create_task(
            crawler(i + 1, crw_ctxs[i], aq, stats, done_urls))
            for i in range(CRAWLERS)]

        # return_exceptions：單一收集緒出錯不會中斷整體
        await asyncio.gather(*collectors, return_exceptions=True)
        log("所有連結收集完成，等待文章佇列清空…")
        await aq.join()                          # 全部文章處理完
        for c in crawlers:
            c.cancel()
        await asyncio.gather(*crawlers, return_exceptions=True)
        save_json(DONE_URLS, sorted(done_urls))
        try:
            await browser.close()
        except Exception:
            pass

    log(f"全部完成：新增 {stats.new} 篇、已存在 {stats.skip} 篇，"
        f"累計完成 {len(done_urls)} 篇")


if __name__ == "__main__":
    asyncio.run(main())
