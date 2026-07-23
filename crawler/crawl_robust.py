"""穩健版爬蟲：多程序 + 每位記者用全新瀏覽器，避免共享瀏覽器劣化卡死。

架構：
- 主程序把記者依站台輪流分配給 W 個子程序（各自獨立的 Python 程序）。
- 每個子程序：對每位記者「啟動全新瀏覽器 → 收集連結 → 多分頁並行抓文章 → 關閉瀏覽器」。
- 全新瀏覽器/記者 = 不累積劣化；程序隔離 = 一個卡住不影響其他。
- 每位記者設 1 小時硬性逾時；每篇文章 90 秒逾時。
- 主程序看門狗：總文章數若長時間無成長，重啟所有子程序（自動續爬）。

用法：
  python crawl_robust.py            # 全部
  python crawl_robust.py chinatimes # 指定站台
  WORKERS=5 ARTCONC=4 python crawl_robust.py
"""
import os
import sys
import time
import signal
import asyncio
import subprocess
import multiprocessing as mp
from playwright.async_api import async_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (log, save_article, load_json, save_json, DATASET, STATE,
                    SITE_NAME, SITE_BASE, UA, parse_date, sanitize)
from sites import CT_ART, CTEE_ART

REPORTERS = DATASET / "記者清單.json"
COMPLETED_LOG = STATE / "robust_completed.log"
WORKERS = int(os.environ.get("WORKERS", "5"))
ARTCONC = int(os.environ.get("ARTCONC", "4"))     # 每位記者同時抓幾篇
REPORTER_TIMEOUT = int(os.environ.get("REPORTER_TIMEOUT", "3600"))
ART_TIMEOUT = 90
STALL_SEC = int(os.environ.get("STALL_SEC", "600"))
MAX_RESTARTS = int(os.environ.get("MAX_RESTARTS", "50"))


def load_completed():
    """讀取已完整處理過的記者（site:reporter_id），重啟時跳過。"""
    done = set()
    if COMPLETED_LOG.exists():
        for line in COMPLETED_LOG.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                done.add(line)
    return done


def mark_completed(key):
    """附加寫入，避免多個子程序同時整檔覆寫造成資料遺失。"""
    STATE.mkdir(parents=True, exist_ok=True)
    with open(COMPLETED_LOG, "a", encoding="utf-8") as f:
        f.write(key + "\n")


def _abs(base, href):
    if not href:
        return None
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return base + href
    return href


async def load(pg, url, cloudflare=False, timeout=40000, settle=1000):
    try:
        await pg.goto(url, wait_until="commit" if cloudflare else "domcontentloaded",
                      timeout=timeout)
    except Exception:
        pass
    if cloudflare:
        for _ in range(30):
            await pg.wait_for_timeout(1000)
            t = await pg.title()
            if t and "moment" not in t.lower() and "請稍候" not in t:
                break
    else:
        await pg.wait_for_timeout(settle)


async def txt(el):
    return (await el.inner_text()).strip() if el else ""


# ----------------------- 連結收集 -----------------------

async def collect_udn(pg, r):
    await load(pg, r["url"])
    h1 = await pg.query_selector("h1")
    name = (await txt(h1)) or r["name"]
    seen = {}
    stagnant = last = 0
    for _ in range(250):
        items = await pg.eval_on_selector_all(
            "div.story-list__holder a[href*='/news/story/']",
            "els => els.map(a => [a.getAttribute('href'), a.innerText])")
        for href, title in items:
            href = (href or "").split("?")[0]
            title = (title or "").strip().replace("\n", " ")
            url = _abs(SITE_BASE["udn"], href)
            if url and title and url not in seen:
                seen[url] = title
        await pg.mouse.wheel(0, 6000)
        await pg.wait_for_timeout(1200)
        if len(seen) == last:
            stagnant += 1
            if stagnant >= 4:
                break
        else:
            stagnant = 0
            last = len(seen)
    return name, list(seen.items())


async def collect_ct(pg, r):
    base = f"{SITE_BASE['chinatimes']}/reporter/{r['reporter_id']}"
    await load(pg, base + "?chdtv", cloudflare=True)
    name = (await pg.title() or "").split(" - ")[0].strip() or r["name"]
    import re
    last = 1
    for a in await pg.query_selector_all(".pagination a"):
        m = re.search(r"page=(\d+)", await a.get_attribute("href") or "")
        if m:
            last = max(last, int(m.group(1)))
    seen = {}
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
                seen[url] = title
    return name, list(seen.items())


async def collect_ctee(pg, r):
    await load(pg, f"{SITE_BASE['ctee']}/reporter/{r['reporter_id']}")
    h1 = await pg.query_selector("h1")
    name = (await txt(h1)).split()[-1] if h1 else r["name"]
    for _ in range(400):
        btn = await pg.query_selector(
            "button:has-text('載入更多'), a:has-text('載入更多')")
        if not btn or not await btn.is_visible():
            break
        try:
            await btn.click()
        except Exception:
            break
        await pg.wait_for_timeout(1200)
    seen = {}
    for a in await pg.query_selector_all("h3 a, a[href*='/news/']"):
        href = await a.get_attribute("href") or ""
        if not CTEE_ART.search(href):
            continue
        url = _abs(SITE_BASE["ctee"], href.split("?")[0])
        title = (await a.inner_text() or "").strip().replace("\n", " ")
        if url and title and url not in seen:
            seen[url] = title
    return name, list(seen.items())


COLLECT = {"udn": collect_udn, "chinatimes": collect_ct, "ctee": collect_ctee}


# ----------------------- 文章解析 -----------------------

async def parse_udn(pg):
    h1 = await pg.query_selector("h1")
    el = await pg.query_selector('meta[property="article:published_time"]')
    date = parse_date(await el.get_attribute("content")) if el else "unknown"
    if date == "unknown":
        t = await pg.query_selector(".article-content__time")
        date = parse_date(await t.inner_text()) if t else "unknown"
    body = await pg.query_selector("section.article-content__editor")
    return (await txt(h1)) or "untitled", date, (await txt(body))


async def parse_ct(pg):
    h1 = await pg.query_selector("h1")
    el = await pg.query_selector('meta[property="article:published_time"]')
    date = parse_date(await el.get_attribute("content")) if el else "unknown"
    if date == "unknown":
        t = await pg.query_selector("time")
        date = parse_date((await t.get_attribute("datetime") or await t.inner_text())) if t else "unknown"
    body = await pg.query_selector("div.article-body")
    return (await txt(h1)) or "untitled", date, (await txt(body))


async def parse_ctee(pg):
    h1 = await pg.query_selector("h1")
    el = await pg.query_selector('meta[property="article:published_time"]')
    date = parse_date(await el.get_attribute("content")) if el else "unknown"
    if date == "unknown":
        t = await pg.query_selector("time, .date")
        date = parse_date((await t.get_attribute("datetime") or await t.inner_text())) if t else "unknown"
    body = (await pg.query_selector("div.article-body")
            or await pg.query_selector("article"))
    return (await txt(h1)) or "untitled", date, (await txt(body))


PARSE = {"udn": parse_udn, "chinatimes": parse_ct, "ctee": parse_ctee}


# ----------------------- 子程序邏輯 -----------------------

def reporter_done_urls(site, name):
    """讀取『該記者資料夾』內已存文章的網址（逐記者去重，確保每位記者都有完整文章）。"""
    folder = DATASET / SITE_NAME[site] / sanitize(name, 60)
    done = set()
    if folder.exists():
        for txt_file in folder.glob("*.txt"):
            try:
                with open(txt_file, encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("網址："):
                            done.add(line.split("網址：", 1)[1].strip())
                            break
            except Exception:
                pass
    return done


async def fetch_save(page, site, name, url, title, cf, done, counter):
    await load(page, url, cloudflare=cf)
    t, date, body = await PARSE[site](page)
    if body:
        # 寫檔丟到獨立執行緒：資料集在 WSL2 DrvFs (/mnt/...) 掛載上，
        # 偶爾同步寫入會卡住；若直接 await 會凍結整個事件迴圈（所有分頁一起卡死）。
        _p, created = await asyncio.to_thread(
            save_article, site, name, date, t or title, url, body)
        done.add(url)
        if created:
            counter[0] += 1


async def run_reporter(browser, r, counter):
    ctx = await browser.new_context(user_agent=UA, locale="zh-TW",
                                    viewport={"width": 1366, "height": 900})
    ctx.set_default_timeout(40000)
    await ctx.add_init_script(
        "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
    cf = (r["site"] == "chinatimes")
    pg = await ctx.new_page()
    name, links = await COLLECT[r["site"]](pg, r)
    done = await asyncio.to_thread(reporter_done_urls, r["site"], name)   # 逐記者去重
    todo = [(u, t) for (u, t) in links if u not in done]
    log(f"  {SITE_NAME[r['site']]}/{name}：連結{len(links)}、待抓{len(todo)}")

    q = asyncio.Queue()
    for item in todo:
        q.put_nowait(item)
    pages = [pg] + [await ctx.new_page() for _ in range(max(0, ARTCONC - 1))]

    async def fworker(page):
        while True:
            try:
                u, t = q.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                await asyncio.wait_for(
                    fetch_save(page, r["site"], name, u, t, cf, done, counter),
                    timeout=ART_TIMEOUT)
            except Exception:
                pass

    await asyncio.gather(*[fworker(p) for p in pages])
    await ctx.close()
    return len(todo)


async def worker_async(wid, reporters):
    counter = [0]
    log(f"[子{wid}] 啟動，負責 {len(reporters)} 位記者")
    async with async_playwright() as p:
        for i, r in enumerate(reporters, 1):
            browser = None
            try:
                browser = await p.chromium.launch()
                await asyncio.wait_for(
                    run_reporter(browser, r, counter),
                    timeout=REPORTER_TIMEOUT)
                log(f"[子{wid}] ({i}/{len(reporters)}) {r['name']} 完成，"
                    f"本程序累計新增 {counter[0]} 篇")
                mark_completed(f"{r['site']}:{r['reporter_id']}")
            except asyncio.TimeoutError:
                log(f"[子{wid}] ⏱ {r['name']} 逾時，跳過")
            except Exception as e:
                log(f"[子{wid}] ! {r['name']} 失敗：{str(e)[:70]}")
            finally:
                if browser:
                    try:
                        await browser.close()
                    except Exception:
                        pass
    log(f"[子{wid}] 全部完成，新增 {counter[0]} 篇")


def worker_main(wid, reporters):
    asyncio.run(worker_async(wid, reporters))


# ----------------------- 主程序（含看門狗） -----------------------

def count_files():
    n = 0
    for _r, _d, files in os.walk(DATASET):
        n += sum(1 for f in files if f.endswith(".txt"))
    return n


def split_reporters(reporters, w):
    buckets = {}
    for r in reporters:
        buckets.setdefault(r["site"], []).append(r)
    ordered = []
    while any(buckets.values()):
        for site in list(buckets):
            if buckets[site]:
                ordered.append(buckets[site].pop(0))
    shards = [[] for _ in range(w)]
    for i, r in enumerate(ordered):
        shards[i % w].append(r)
    return shards


def spawn_workers(shards):
    procs = []
    for wid, shard in enumerate(shards):
        if not shard:
            continue
        pr = mp.Process(target=worker_main, args=(wid + 1, shard))
        pr.start()
        procs.append(pr)
    return procs


def kill_all(procs):
    """OS 層級強殺，不依賴子程序內部是否還聽得到取消訊號。"""
    for pr in procs:
        if pr.is_alive():
            pr.terminate()
    time.sleep(2)
    for pr in procs:
        if pr.is_alive():
            pr.kill()
    for pr in procs:
        pr.join(timeout=5)
    try:
        subprocess.run(["pkill", "-9", "-f", "chromium"],
                        stderr=subprocess.DEVNULL, timeout=15)
    except subprocess.TimeoutExpired:
        log("  ⚠ pkill -f chromium 逾時（可能有卡在 D 狀態的殭屍進程），跳過")


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else "all"
    # 全部完成後不馬上結束：定期重讀記者清單，讓 discover.py 在背景找到的
    # 新記者能自動被排進來，不必手動重啟。IDLE_EXIT 秒內都沒有新記者才真正結束。
    idle_poll = int(os.environ.get("IDLE_POLL", "60"))
    idle_exit = int(os.environ.get("IDLE_EXIT", "1800"))

    attempt = 0
    idle_since = None
    while attempt < MAX_RESTARTS:
        reporters = load_json(REPORTERS, [])
        if only != "all":
            reporters = [r for r in reporters if r["site"] == only]
        completed = load_completed()
        todo = [r for r in reporters
                if f"{r['site']}:{r['reporter_id']}" not in completed]

        if not todo:
            now = time.time()
            if idle_since is None:
                idle_since = now
                log(f"目前清單已全部完成（{count_files()} 篇）。"
                    f"每 {idle_poll}s 檢查一次記者清單是否有新增，"
                    f"{idle_exit}s 內都沒有才會真正結束…")
            elif now - idle_since >= idle_exit:
                log(f"閒置 {idle_exit}s 無新記者，結束。總計 {count_files()} 篇")
                return
            time.sleep(idle_poll)
            continue
        idle_since = None

        attempt += 1
        shards = split_reporters(todo, WORKERS)
        log(f"穩健爬蟲啟動（第 {attempt} 次）：剩餘 {len(todo)}/{len(reporters)} 位記者，"
            f"{WORKERS} 個子程序，每位記者 {ARTCONC} 分頁並抓。目前 {count_files()} 篇")
        procs = spawn_workers(shards)

        # 看門狗：總檔案數長時間無成長 → 強殺子程序（含瀏覽器）並重啟，跳過已完成記者續爬
        last = count_files()
        last_t = time.time()
        stalled = False
        while any(pr.is_alive() for pr in procs):
            time.sleep(30)
            now = count_files()
            if now > last:
                last = now
                last_t = time.time()
            elif time.time() - last_t > STALL_SEC:
                log(f"⚠ 已 {STALL_SEC}s 無新檔（{now} 篇），強殺子程序並重啟…")
                kill_all(procs)
                stalled = True
                break
        if not stalled:
            for pr in procs:
                pr.join()
        time.sleep(3)

    log(f"達到最大重啟次數（{MAX_RESTARTS}），停止。目前 {count_files()} 篇")


if __name__ == "__main__":
    main()
