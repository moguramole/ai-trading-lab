"""日足CSVの読み込み・検証・SQLite保存。

CSV列: date,ticker,open,high,low,close,volume
"""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

COLUMNS = ["date", "ticker", "open", "high", "low", "close", "volume"]
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "lab.db"


class DataError(ValueError):
    pass


def validate(df: pd.DataFrame) -> pd.DataFrame:
    """欠損・重複・不正価格・並び順を検査して整形済みDataFrameを返す。"""
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise DataError(f"列が足りない: {missing}")
    df = df[COLUMNS].copy()
    df["ticker"] = df["ticker"].astype(str)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for c in COLUMNS[2:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    if df.isna().any().any():
        raise DataError("欠損または数値でない値がある")
    if df.duplicated(["ticker", "date"]).any():
        raise DataError("同じ銘柄・日付が重複している")
    if (df[["open", "high", "low", "close"]] <= 0).any().any():
        raise DataError("価格が0以下の行がある")
    if (df["volume"] < 0).any():
        raise DataError("出来高が負")
    if (df["high"] < df[["open", "close", "low"]].max(axis=1)).any() or \
       (df["low"] > df[["open", "close", "high"]].min(axis=1)).any():
        raise DataError("高値・安値が始値/終値と矛盾")
    return df.sort_values(["ticker", "date"]).reset_index(drop=True)


JUMP = 0.40            # 日本株は値幅制限があり、1日で40%超動くのは分割か異常値とみなす
SPLIT_TOL = 0.05       # 変動比が整数倍に5%以内で近ければ分割とみなす
SPLIT_MAX = 20
ABSURD_PRICE = 10_000_000  # 日本株の最高値はこれよりずっと低い
FLAG_COLUMNS = ["ticker", "date", "kind", "detail"]


def _clean_ticker(g: pd.DataFrame):
    flags = []
    g = g.astype({c: float for c in COLUMNS[2:]})  # 分割調整で小数倍になるため、整数列も実数にする
    # (0) 日本株にあり得ない価格の行（再上場前の無効データなど）は先に捨てる
    absurd = (g[["open", "high", "low", "close"]] > ABSURD_PRICE).any(axis=1)
    if absurd.any():
        flags.append((g["ticker"].iloc[0], g.loc[absurd, "date"].iloc[0].strftime("%Y-%m-%d"),
                      "無効な価格を除去", f"{int(absurd.sum())}行"))
        g = g[~absurd].reset_index(drop=True)
    for _ in range(20):  # 1銘柄に複数の異常があっても順に処理する
        c = g["close"].to_numpy()
        big = np.flatnonzero(np.abs(c[1:] / c[:-1] - 1) > JUMP)
        if big.size == 0:
            break
        i, t = big[0] + 1, g["ticker"].iloc[0]
        date = g["date"].iloc[i].strftime("%Y-%m-%d")
        # (1) 一時的な異常値: 3行以内に元の水準へ戻るなら、その行を取り除く
        back = next((j for j in range(i + 1, min(i + 4, len(g))) if abs(c[j] / c[i - 1] - 1) <= JUMP), None)
        if back is not None:
            flags.append((t, date, "異常値を除去", f"{back - i}行（終値 {c[i]:.4g}）"))
            g = g.drop(g.index[i:back]).reset_index(drop=True)
            continue
        # (2) 分割・併合: 変動比が整数に近い → それより前の価格と出来高を調整
        ratio = c[i - 1] / c[i]
        n = round(ratio) if ratio >= 1 else round(1 / ratio)
        if 2 <= n <= SPLIT_MAX and abs((ratio if ratio >= 1 else 1 / ratio) / n - 1) <= SPLIT_TOL:
            f = n if ratio >= 1 else 1 / n
            g = g.copy()
            g.loc[:i - 1, ["open", "high", "low", "close"]] /= f
            g.loc[:i - 1, "volume"] *= f
            flags.append((t, date, "分割調整", f"{'分割' if ratio >= 1 else '併合'} {n}倍と推定"))
            continue
        # (3) 原因不明の水準のずれ: 分析から除外
        flags.append((t, date, "銘柄を除外", f"原因不明の価格のずれ（比 {ratio:.2f}）"))
        return g.iloc[0:0], flags
    return g, flags


def clean(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """異常値の除去・分割調整・除外。(整えたデータ, 処理内容の一覧) を返す。元データは変えない。"""
    df = df.sort_values(["ticker", "date"]).reset_index(drop=True)
    parts, flags = [], []
    for _, g in df.groupby("ticker", sort=False):
        out, fl = _clean_ticker(g.reset_index(drop=True))
        parts.append(out)
        flags += fl
    cleaned = pd.concat(parts, ignore_index=True) if parts else df
    return cleaned, pd.DataFrame(flags, columns=FLAG_COLUMNS)


def load_csv(path) -> pd.DataFrame:
    return validate(pd.read_csv(path))


def zero_volume_days(df: pd.DataFrame) -> pd.DataFrame:
    """出来高ゼロの行（休場や異常の可能性）。エラーにはせず確認用に返す。"""
    return df[df["volume"] == 0]


def connect(db_path=DB_PATH) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("""CREATE TABLE IF NOT EXISTS prices (
        ticker TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL,
        source TEXT, fetched_at TEXT, tz TEXT DEFAULT 'Asia/Tokyo',
        PRIMARY KEY (ticker, date))""")
    return con


def save_prices(con, df: pd.DataFrame, source: str) -> int:
    """検証済みデータをUPSERT。データソースと取得時刻(UTC)も保存。"""
    now = datetime.now(timezone.utc).isoformat()
    rows = [(r.ticker, r.date.strftime("%Y-%m-%d"), r.open, r.high, r.low, r.close,
             r.volume, source, now) for r in df.itertuples()]
    con.executemany("""INSERT OR REPLACE INTO prices
        (ticker,date,open,high,low,close,volume,source,fetched_at) VALUES (?,?,?,?,?,?,?,?,?)""", rows)
    con.commit()
    return len(rows)


def read_prices(con) -> pd.DataFrame:
    df = pd.read_sql_query("SELECT * FROM prices ORDER BY ticker, date", con)
    df["date"] = pd.to_datetime(df["date"])
    return df


def read_clean(con) -> tuple[pd.DataFrame, pd.DataFrame]:
    """分析用の価格(列は COLUMNS)と、処理内容の一覧。DBが空なら空のDataFrame。"""
    raw = read_prices(con)
    if raw.empty:
        return pd.DataFrame(columns=COLUMNS), pd.DataFrame(columns=FLAG_COLUMNS)
    cleaned, flags = clean(raw[COLUMNS])
    return validate(cleaned), flags
