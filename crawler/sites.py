"""各新聞網的結構解析：記者發現、文章列表、文章內頁。"""
import re
from common import load, log, parse_date, SITE_BASE

CT_ART = re.compile(r"/(?:realtimenews|newspapers|opinion)/\d{14}-\d+")
CTEE_ART = re.compile(r"/news/\d{14}-\d+")


def _abs(base, href):
    if not href:
        return None
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return base + href
    return href


# ----------------------------- 聯合新聞網 (UDN) -----------------------------

def udn_hot_reporters(pg):
    """從記者頁的『熱門記者』區取得 {reporter_id(code): name}。"""
    out = {}
    for a in pg.query_selector_all("a[href*='/news/reporter/']"):
        href = a.get_attribute("href") or ""
        txt = (a.inner_text() or "").strip()
        m = re.search(r"/news/reporter/([^/?#]+)", href)
        if not m or not txt:
            continue
        code = m.group(1)
        # 文字格式為 "姓名 文章數"，取姓名
        name = re.split(r"\s+\d", txt)[0].strip() or txt.split()[0]
        out[code] = name
    return out


def udn_collect_authors_from_feed(pg, scrolls=20):
    """掃描即時新聞（無限滾動），造訪文章取得作者 {reporter_id(code): name}。"""
    load(pg, f"{SITE_BASE['udn']}/news/breaknews/1")
    art_urls = set()
    stagnant = last = 0
    for i in range(scrolls):
        for a in pg.query_selector_all("a[href*='/news/story/']"):
            href = (a.get_attribute("href") or "").split("?")[0]
            if href:
                art_urls.add(_abs(SITE_BASE["udn"], href))
        pg.mouse.wheel(0, 6000)
        pg.wait_for_timeout(1200)
        if len(art_urls) == last:
            stagnant += 1
            if stagnant >= 4:
                break
        else:
            stagnant = 0
            last = len(art_urls)
    log(f"  UDN 即時新聞收集到 {len(art_urls)} 篇文章，造訪取得作者…")
    found = {}
    for i, u in enumerate(sorted(art_urls), 1):
        load(pg, u)
        rid, name = udn_author(pg)
        if rid and name:
            found[rid] = name
        if i % 25 == 0:
            log(f"    已造訪 {i}/{len(art_urls)}，目前 {len(found)} 位記者")
    return found


def udn_author(pg):
    a = pg.query_selector(".article-content__author a[href*='/news/reporter/']")
    if not a:
        return None, None
    href = a.get_attribute("href") or ""
    m = re.search(r"/news/reporter/([^/?#]+)", href)
    name = (a.inner_text() or "").strip()
    return (m.group(1) if m else None), (name or None)


def udn_reporter_name(pg):
    h1 = pg.query_selector("h1")
    return (h1.inner_text().strip() if h1 else "") or "unknown"


def udn_article_links(pg, max_scroll=200):
    """無限滾動載入，回傳該記者所有文章 [(url, title)]。"""
    seen = {}
    stagnant = 0
    last = 0
    for i in range(max_scroll):
        for a in pg.query_selector_all(
                "div.context-box__content a[href*='/news/story/']"):
            href = (a.get_attribute("href") or "").split("?")[0]
            if not href:
                continue
            url = _abs(SITE_BASE["udn"], href)
            title = (a.inner_text() or "").strip().replace("\n", " ")
            if url not in seen and title:
                seen[url] = title
        pg.mouse.wheel(0, 6000)
        pg.wait_for_timeout(1300)
        if len(seen) == last:
            stagnant += 1
            if stagnant >= 4:
                break
        else:
            stagnant = 0
            last = len(seen)
        if (i + 1) % 15 == 0:
            log(f"    …滾動 {i + 1} 次，已收集 {len(seen)} 篇連結")
    return list(seen.items())


def udn_parse_article(pg):
    h1 = pg.query_selector("h1")
    title = h1.inner_text().strip() if h1 else "untitled"
    date = "unknown"
    el = pg.query_selector('meta[property="article:published_time"]')
    if el:
        date = parse_date(el.get_attribute("content"))
    if date == "unknown":
        el = pg.query_selector(".article-content__time")
        if el:
            date = parse_date(el.inner_text())
    body_el = pg.query_selector("section.article-content__editor")
    body = body_el.inner_text().strip() if body_el else ""
    return title, date, body


# ----------------------------- 中時新聞網 (ChinaTimes) -----------------------------

def ct_collect_authors_from_feed(pg, pages):
    """掃描即時新聞列表，造訪文章取得作者 {reporter_id: name}。"""
    found = {}
    art_urls = set()
    for page in pages:
        url = f"https://www.chinatimes.com/realtimenews/?page={page}&chdtv"
        load(pg, url, cloudflare=True)
        for a in pg.query_selector_all("a"):
            href = a.get_attribute("href") or ""
            if CT_ART.search(href):
                art_urls.add(_abs(SITE_BASE["chinatimes"], href.split("?")[0]))
    log(f"  中時即時新聞收集到 {len(art_urls)} 篇文章，造訪取得作者…")
    for i, u in enumerate(sorted(art_urls), 1):
        load(pg, u, cloudflare=True)
        rid, name = ct_author(pg)
        if rid and name:
            found[rid] = name
        if i % 25 == 0:
            log(f"    已造訪 {i}/{len(art_urls)}，目前 {len(found)} 位記者")
    return found


def ct_author(pg):
    a = pg.query_selector("div.author a, .author a[href*='/reporter/']")
    if not a:
        return None, None
    href = a.get_attribute("href") or ""
    m = re.search(r"/reporter/(\d+)", href)
    name = (a.inner_text() or "").strip()
    return (m.group(1) if m else None), (name or None)


def ct_reporter_name(pg):
    t = pg.title() or ""
    return t.split(" - ")[0].strip() or "unknown"


def ct_last_page(pg):
    last = 1
    for a in pg.query_selector_all(".pagination a"):
        href = a.get_attribute("href") or ""
        m = re.search(r"page=(\d+)", href)
        if m:
            last = max(last, int(m.group(1)))
    return last


def ct_article_links(pg, reporter_id, max_pages=300):
    """逐頁取得該記者所有文章 [(url, title)]。"""
    seen = {}
    base = f"https://www.chinatimes.com/reporter/{reporter_id}"
    load(pg, base + "?chdtv", cloudflare=True)
    last = min(ct_last_page(pg), max_pages)
    for page in range(1, last + 1):
        if page > 1:
            load(pg, f"{base}?page={page}", cloudflare=True)
        for a in pg.query_selector_all("section.article-list h3 a, h3 a"):
            href = a.get_attribute("href") or ""
            if not CT_ART.search(href):
                continue
            url = _abs(SITE_BASE["chinatimes"], href.split("?")[0])
            title = (a.inner_text() or "").strip().replace("\n", " ")
            if url and title and url not in seen:
                seen[url] = title
    return list(seen.items())


def ct_parse_article(pg):
    h1 = pg.query_selector("h1")
    title = h1.inner_text().strip() if h1 else "untitled"
    date = "unknown"
    el = pg.query_selector('meta[property="article:published_time"]')
    if el:
        date = parse_date(el.get_attribute("content"))
    if date == "unknown":
        el = pg.query_selector("time")
        if el:
            date = parse_date(el.get_attribute("datetime") or el.inner_text())
    body_el = pg.query_selector("div.article-body")
    body = body_el.inner_text().strip() if body_el else ""
    return title, date, body


# ----------------------------- 工商時報 (CTEE) -----------------------------

def _ctee_click_more(pg, max_clicks):
    for _ in range(max_clicks):
        btn = pg.query_selector(
            "button:has-text('載入更多'), a:has-text('載入更多')")
        if not btn or not btn.is_visible():
            break
        try:
            btn.click()
        except Exception:
            break
        pg.wait_for_timeout(1400)


def ctee_collect_authors_from_feed(pg, max_clicks=30):
    """掃描即時新聞（載入更多），造訪文章取得作者 {reporter_id: name}。"""
    load(pg, "https://www.ctee.com.tw/livenews")
    _ctee_click_more(pg, max_clicks)
    art_urls = set()
    for a in pg.query_selector_all("a"):
        href = a.get_attribute("href") or ""
        if CTEE_ART.search(href):
            art_urls.add(_abs(SITE_BASE["ctee"], href.split("?")[0]))
    log(f"  工商即時新聞收集到 {len(art_urls)} 篇文章，造訪取得作者…")
    found = {}
    for i, u in enumerate(sorted(art_urls), 1):
        load(pg, u)
        rid, name = ctee_author(pg)
        if rid and name:
            found[rid] = name
        if i % 25 == 0:
            log(f"    已造訪 {i}/{len(art_urls)}，目前 {len(found)} 位記者")
    return found


def ctee_author(pg):
    a = pg.query_selector("a[href*='/reporter/']")
    if not a:
        return None, None
    href = a.get_attribute("href") or ""
    m = re.search(r"/reporter/(\d+)", href)
    name = (a.inner_text() or "").strip()
    return (m.group(1) if m else None), (name or None)


def ctee_reporter_name(pg):
    h1 = pg.query_selector("h1")
    if h1:
        return (h1.inner_text() or "").strip().split()[-1]
    return (pg.title() or "").split(" - ")[0].strip() or "unknown"


def ctee_article_links(pg, reporter_id, max_clicks=400):
    """點『載入更多』直到結束，取得該記者所有文章 [(url, title)]。"""
    load(pg, f"https://www.ctee.com.tw/reporter/{reporter_id}")
    _ctee_click_more(pg, max_clicks)
    seen = {}
    for a in pg.query_selector_all("h3 a, a[href*='/news/']"):
        href = a.get_attribute("href") or ""
        if not CTEE_ART.search(href):
            continue
        url = _abs(SITE_BASE["ctee"], href.split("?")[0])
        title = (a.inner_text() or "").strip().replace("\n", " ")
        if url and title and url not in seen:
            seen[url] = title
    return list(seen.items())


def ctee_parse_article(pg):
    h1 = pg.query_selector("h1")
    title = h1.inner_text().strip() if h1 else "untitled"
    date = "unknown"
    el = pg.query_selector('meta[property="article:published_time"]')
    if el:
        date = parse_date(el.get_attribute("content"))
    if date == "unknown":
        el = pg.query_selector("time, .date")
        if el:
            date = parse_date(el.get_attribute("datetime") or el.inner_text())
    body_el = (pg.query_selector("div.article-body")
               or pg.query_selector("article"))
    body = body_el.inner_text().strip() if body_el else ""
    return title, date, body
