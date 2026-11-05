"""適時開示(TDnet)の取得・保存・依頼文用の抽出。

取得元はJPXの「適時開示情報閲覧サービス」の公開ページ（直近約1か月分）。題名だけを使い、
本文(PDF)は取得しない。1日あたり数リクエストにとどめ、ページ間は待つ。
取得元を替えるときはこのモジュールだけ差し替える。
"""
import math
import re
import time as _time
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from . import jpcal, schedule

BASE = "https://www.release.tdnet.info/inbs/"
PER_PAGE = 100
COLUMNS = ["url", "date", "time", "ticker", "name", "title"]
_ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
_CELL = {k: re.compile(rf'class="[^"]*{k}[^"]*"[^>]*>(.*?)</td>', re.S)
         for k in ("kjTime", "kjCode", "kjName", "kjTitle")}
_HREF = re.compile(r'href="([^"]+)"')
_TAG = re.compile(r"<[^>]+>")
_TOTAL = re.compile(r"全\s*(\d+)\s*件")


def _text(s: str) -> str:
    return re.sub(r"\s+", " ", _TAG.sub("", s)).strip()


def parse_page(html: str, day: date) -> pd.DataFrame:
    """一覧ページ1枚分を DataFrame(COLUMNS) にする。コードは5桁なので末尾の0を除いた4桁を銘柄コードとする。"""
    rows = []
    for tr in _ROW.findall(html):
        if "kjTitle" not in tr:
            continue
        cells = {k: p.search(tr) for k, p in _CELL.items()}
        href = _HREF.search(cells["kjTitle"].group(1)) if cells["kjTitle"] else None
        if not all(cells.values()) or not href:
            continue
        code = _text(cells["kjCode"].group(1))
        rows.append((BASE + href.group(1), day.isoformat(), _text(cells["kjTime"].group(1)),
                     code[:4] if len(code) == 5 else code, _text(cells["kjName"].group(1)),
                     _text(cells["kjTitle"].group(1))))
    return pd.DataFrame(rows, columns=COLUMNS)


def total_count(html: str) -> int:
    m = _TOTAL.search(html)
    return int(m.group(1)) if m else 0


def fetch_day(day: date, get=None, pause=1.0) -> pd.DataFrame:
    """指定日の開示を全ページ取得する。その日の開示がまだない/休場日は空のDataFrame。"""
    if get is None:
        import requests

        def get(url):
            r = requests.get(url, timeout=20, headers={"User-Agent": "ai-trading-lab (personal research)"})
            if r.status_code == 404:
                return None
            r.raise_for_status()
            r.encoding = "utf-8"
            return r.text
    ymd = day.strftime("%Y%m%d")
    first = get(f"{BASE}I_list_001_{ymd}.html")
    if not first:
        return pd.DataFrame(columns=COLUMNS)
    pages = [parse_page(first, day)]
    for n in range(2, math.ceil(total_count(first) / PER_PAGE) + 1):
        _time.sleep(pause)
        html = get(f"{BASE}I_list_{n:03d}_{ymd}.html")
        if html:
            pages.append(parse_page(html, day))
    return pd.concat(pages, ignore_index=True).drop_duplicates("url")


def ensure(con):
    con.execute("""CREATE TABLE IF NOT EXISTS disclosures (
        url TEXT PRIMARY KEY, date TEXT, time TEXT, ticker TEXT, name TEXT, title TEXT, fetched_at TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS prompt_news (
        model TEXT, as_of TEXT, url TEXT, PRIMARY KEY (model, as_of, url))""")


def save(con, df: pd.DataFrame) -> int:
    """追記専用。同じURLは書き換えない。新規件数を返す。"""
    ensure(con)
    now = datetime.now(timezone.utc).isoformat()
    before = con.total_changes
    con.executemany("INSERT OR IGNORE INTO disclosures VALUES (?,?,?,?,?,?,?)",
                    [(r.url, r.date, r.time, r.ticker, r.name, r.title, now) for r in df.itertuples()])
    con.commit()
    return con.total_changes - before


def update(con, today: date, days: int = 2, get=None) -> int:
    """直近days営業日分（当日含む）を取得して保存する。"""
    day, n, fetched = today, 0, 0
    while fetched < days:
        if jpcal.is_business_day(day):
            n += save(con, fetch_day(day, get))
            fetched += 1
        day -= timedelta(days=1)
    return n


def for_prompt(con, tickers, as_of: date, per_ticker=3, limit=40) -> pd.DataFrame:
    """依頼文に載せる開示。前営業日の15:30以降〜判断日（as_of）の21:30まで、対象銘柄だけ。

    判断日の21:30より後に出た開示と、翌営業日以降の開示は含めない（いつ依頼文を作っても同じ内容になる）。
    tickersの先頭ほど優先（保有銘柄を先に渡す）。
    """
    cutoff = f"{schedule.DAILY_RUN:%H:%M}"
    ensure(con)
    prev = jpcal.last_business_day(pd.Timestamp(as_of) - pd.Timedelta(days=1))
    tickers = list(dict.fromkeys(tickers))
    if not tickers:
        return pd.DataFrame(columns=COLUMNS)
    q = ",".join("?" * len(tickers))
    df = pd.read_sql_query(
        f"SELECT url,date,time,ticker,name,title FROM disclosures WHERE ticker IN ({q}) "
        "AND (date<? OR (date=? AND time<=?)) AND (date>? OR (date=? AND time>='15:30'))",
        con, params=[*tickers, as_of.isoformat(), as_of.isoformat(), cutoff, prev.isoformat(), prev.isoformat()])
    rank = {t: i for i, t in enumerate(tickers)}
    df["rank"] = df["ticker"].map(rank)
    df = df.sort_values(["rank", "date", "time"], ascending=[True, False, False])
    return df.groupby("ticker", sort=False).head(per_ticker).head(limit).drop(columns="rank").reset_index(drop=True)


def record_used(con, model: str, as_of: str, urls) -> None:
    """依頼文に載せた開示を、(モデル, 判断日)ごとに記録する。"""
    ensure(con)
    con.executemany("INSERT OR IGNORE INTO prompt_news VALUES (?,?,?)", [(model, as_of, u) for u in urls])
    con.commit()


def load(con, since: str) -> pd.DataFrame:
    """since(YYYY-MM-DD)以降の開示を、新しい順に返す。"""
    ensure(con)
    return pd.read_sql_query("SELECT url,date,time,ticker,name,title FROM disclosures WHERE date>=? "
                             "ORDER BY date DESC, time DESC", con, params=[since])


def used_urls(con) -> set[str]:
    """これまでにAIの依頼文に載せた開示のURL。"""
    ensure(con)
    return {r[0] for r in con.execute("SELECT DISTINCT url FROM prompt_news")}


def used_for(con, model: str, as_of: str) -> pd.DataFrame:
    """そのモデルのその判断日の依頼文に載せた開示（登録時に記録したもの）。"""
    ensure(con)
    return pd.read_sql_query(
        "SELECT d.url,d.date,d.time,d.ticker,d.name,d.title FROM prompt_news p "
        "JOIN disclosures d ON d.url=p.url WHERE p.model=? AND p.as_of=? ORDER BY d.date DESC, d.time DESC",
        con, params=[model, as_of])

