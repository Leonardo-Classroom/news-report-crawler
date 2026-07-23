"""共用工具：瀏覽器設定、頁面載入（含 Cloudflare 處理）、檔名清理、文章儲存。"""
import re
import json
import pathlib
import datetime

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATASET = ROOT / "dataset"
STATE = DATASET / "_state"

# 站台代碼 -> 中文新聞網名稱（同時作為資料夾名稱）
SITE_NAME = {
    "udn": "聯合新聞網",
    "chinatimes": "中時新聞網",
    "ctee": "工商時報",
}
SITE_BASE = {
    "udn": "https://udn.com",
    "chinatimes": "https://www.chinatimes.com",
    "ctee": "https://www.ctee.com.tw",
}


def make_context(p, headless=True):
    """建立帶有反偵測設定的瀏覽器 context。"""
    browser = p.chromium.launch(headless=headless)
    ctx = browser.new_context(user_agent=UA, locale="zh-TW",
                              viewport={"width": 1366, "height": 900})
    ctx.add_init_script(
        "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
    return browser, ctx


def load(pg, url, cloudflare=False, timeout=45000, settle=1500):
    """載入頁面。cloudflare=True 時等待 'Just a moment' 挑戰通過。"""
    try:
        pg.goto(url, wait_until="commit" if cloudflare else "domcontentloaded",
                timeout=timeout)
    except Exception:
        pass
    if cloudflare:
        for _ in range(35):
            pg.wait_for_timeout(1000)
            t = pg.title()
            if t and "moment" not in t.lower() and "請稍候" not in t:
                break
    else:
        pg.wait_for_timeout(settle)
    return pg.title()


def sanitize(name, maxlen=80):
    """清理檔名/資料夾名中的非法字元。"""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", str(name)).strip()
    name = re.sub(r"\s+", " ", name)
    name = name.strip(". ")
    return name[:maxlen].strip() or "untitled"


def parse_date(raw):
    """從各種日期字串抽出 YYYY-MM-DD。失敗回傳 'unknown'。"""
    if not raw:
        return "unknown"
    m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", raw)
    if m:
        y, mo, d = m.groups()
        return f"{y}-{int(mo):02d}-{int(d):02d}"
    return "unknown"


def reporter_dir(site, reporter_name):
    d = DATASET / SITE_NAME[site] / sanitize(reporter_name, 60)
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_article(site, reporter_name, date, title, url, body):
    """存成 dataset/<新聞網>/<記者>/<日期>-<標題>.txt。回傳 (path, 是否新建)。"""
    d = reporter_dir(site, reporter_name)
    fname = f"{date}-{sanitize(title)}.txt"
    path = d / fname
    if path.exists():
        return path, False
    content = (
        f"標題：{title}\n"
        f"記者：{reporter_name}\n"
        f"新聞網：{SITE_NAME[site]}\n"
        f"日期：{date}\n"
        f"網址：{url}\n"
        f"{'-' * 40}\n"
        f"{body.strip()}\n"
    )
    path.write_text(content, encoding="utf-8")
    return path, True


def load_json(path, default):
    path = pathlib.Path(path)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def save_json(path, obj):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2),
                    encoding="utf-8")


GENERIC_BYLINES = {
    "數位編輯", "綜合報導", "編輯", "編譯", "中時新聞網", "工商時報",
    "聯合新聞網", "本報訊", "中央社", "即時", "中時電子報", "工商即時",
    "記者", "編輯中心", "影音編輯", "網路新聞中心",
}


def is_generic_byline(name):
    """判斷是否為通用署名（非實際記者）。"""
    n = (name or "").strip()
    return (not n) or (n in GENERIC_BYLINES) or len(n) > 12


LOG_FILE = STATE / "crawl.log"


def log(msg):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
