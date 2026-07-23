"""監督器：執行並行爬蟲，偵測卡死自動重啟（續爬），完成則停止。

- 每 30 秒檢查一次資料夾文章數。
- 若爬蟲程序正常結束（exit 0）→ 視為完成，停止。
- 若連續 STALL 秒沒有新文章 → 殺掉重啟（程式會自動續爬、跳過已完成）。
- 若程序自行崩潰 → 重啟。

用法：
  python supervisor.py            # 全部站台
  python supervisor.py chinatimes # 指定站台
"""
import os
import sys
import time
import signal
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
DATASET = os.path.join(HERE, "..", "dataset")
STALL = int(os.environ.get("STALL", "240"))        # 無新檔超過幾秒視為卡死
CHECK = 30                                          # 檢查間隔（秒）
MAX_RESTARTS = int(os.environ.get("MAX_RESTARTS", "100"))


def count_files():
    n = 0
    for _root, _dirs, files in os.walk(DATASET):
        n += sum(1 for f in files if f.endswith(".txt"))
    return n


def log(msg):
    t = time.strftime("%H:%M:%S")
    line = f"[{t}] [監督] {msg}"
    print(line, flush=True)
    try:
        with open(os.path.join(DATASET, "_state", "supervisor.log"), "a",
                  encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def main():
    site = sys.argv[1] if len(sys.argv) > 1 else "all"
    env = dict(os.environ)
    env.setdefault("COLLECTORS", "8")
    env.setdefault("CRAWLERS", "5")

    for attempt in range(1, MAX_RESTARTS + 1):
        log(f"啟動爬蟲（第 {attempt} 次），目前 {count_files()} 篇")
        conda = "/home/leonardo890229/anaconda3/bin/conda"
        proc = subprocess.Popen(
            [conda, "run", "-n", "leo3.10", "python", "-u",
             "crawl_parallel.py", site],
            cwd=HERE, env=env, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)

        last_count = count_files()
        last_progress = time.time()
        while True:
            try:
                ret = proc.wait(timeout=CHECK)
            except subprocess.TimeoutExpired:
                ret = None
            now = count_files()
            if now > last_count:
                last_count = now
                last_progress = time.time()
            if ret is not None:                      # 程序已結束
                if ret == 0:
                    log(f"爬蟲正常完成，共 {now} 篇。監督結束。")
                    return
                log(f"爬蟲異常結束(code={ret})，{now} 篇，準備重啟…")
                break
            if time.time() - last_progress > STALL:  # 卡死
                log(f"偵測到卡死（{STALL}s 無新檔，停在 {now} 篇），殺掉重啟…")
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except Exception:
                    proc.kill()
                subprocess.run(["pkill", "-9", "chromium"],
                               stderr=subprocess.DEVNULL)
                time.sleep(5)
                break
        time.sleep(3)

    log("達到最大重啟次數，停止。")


if __name__ == "__main__":
    main()
