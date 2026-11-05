"""対象銘柄(ユニバース)をJPX公式の上場銘柄一覧から決める。

TOPIX500 (Core30 + Large70 + Mid400) を使う。流動性が高く市場全体の代表になる。
一覧は週1回だけ再取得し、失敗したら前回の保存分を使う。
"""
import io
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

PAGE = "https://www.jpx.co.jp/markets/statistics-equities/misc/01.html"
FILE = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xlsx"
SIZES = ("TOPIX Core30", "TOPIX Large70", "TOPIX Mid400")
REFRESH_DAYS = 7


def select(raw: pd.DataFrame) -> pd.DataFrame:
    """JPXの一覧からTOPIX500を抜き出し、アプリ共通の列に整える。"""
    df = raw[raw["規模区分"].isin(SIZES)]
    out = pd.DataFrame({
        "ticker": df["コード"].astype(str).str.strip(),
        "name": df["銘柄名"].astype(str),
        "sector33": df["33業種区分"].astype(str),
        "size": df["規模区分"].astype(str),
    })
    if out.empty:
        raise RuntimeError("TOPIX500の銘柄が見つからない（一覧の形式が変わった可能性）")
    return out.drop_duplicates("ticker").reset_index(drop=True)


def download() -> pd.DataFrame:
    r = requests.get(FILE, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return select(pd.read_excel(io.BytesIO(r.content), dtype=str))


def ensure(con):
    con.execute("""CREATE TABLE IF NOT EXISTS universe (
        ticker TEXT PRIMARY KEY, name TEXT, sector33 TEXT, size TEXT, updated_at TEXT)""")


def load(con) -> pd.DataFrame:
    ensure(con)
    return pd.read_sql_query("SELECT ticker,name,sector33,size,updated_at FROM universe ORDER BY ticker", con)


def refresh(con, now: datetime | None = None, fetcher=download) -> tuple[pd.DataFrame, str]:
    """必要なら再取得して保存し、(一覧, 状態メッセージ) を返す。失敗時は保存済みを使う。"""
    now = now or datetime.now(timezone.utc)
    cur = load(con)
    if len(cur) and now - datetime.fromisoformat(cur["updated_at"].max()) < timedelta(days=REFRESH_DAYS):
        return cur, "保存済みの一覧を使用"
    try:
        new = fetcher()
    except Exception as e:
        return cur, f"一覧の更新に失敗（{e}）。保存済みを使用"
    stamp = now.isoformat()
    con.execute("DELETE FROM universe")
    con.executemany("INSERT INTO universe VALUES (?,?,?,?,?)",
                    [(r.ticker, r.name, r.sector33, r.size, stamp) for r in new.itertuples()])
    con.commit()
    return load(con), f"JPXの一覧を更新（{len(new)}銘柄）"
