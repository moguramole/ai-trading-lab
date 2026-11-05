"""株価の自動取得（yfinance経由のYahoo Finance。非公式のため個人の研究用途に限る）。

取得元を替えるときはこのモジュールだけ差し替える。
"""
from datetime import datetime, time, timedelta, timezone

import pandas as pd

from . import jpcal
from .data import COLUMNS

SOURCE = "yfinance"
JST = timezone(timedelta(hours=9))
MARKET_CLOSE = time(15, 45)  # 東証の大引け15:30 + 余裕
BENCHMARKS = {"^N225": "日経平均", "1306": "TOPIX連動ETF"}  # 売買対象にしない市場の指標


def read_watchlist(path) -> list[str]:
    lines = [l.split("#")[0].strip() for l in open(path, encoding="utf-8")]
    return [l for l in lines if l]


def last_complete_date(now: datetime | None = None) -> pd.Timestamp:
    """まだ確定していない当日分を保存しないための基準日（JST）。"""
    now = (now or datetime.now(JST)).astimezone(JST)
    d = now.date() if now.time() >= MARKET_CLOSE else now.date() - timedelta(days=1)
    return pd.Timestamp(jpcal.last_business_day(d))  # 休場日なら直前の営業日


def yahoo_symbol(ticker: str) -> str:
    return ticker if ticker.startswith("^") else f"{ticker}.T"


def _clean(sub: pd.DataFrame, ticker: str, now) -> pd.DataFrame:
    if sub is None or sub.empty:
        raise RuntimeError("データが空")
    df = sub.reset_index().rename(columns=str.lower).rename(columns={"price": "date"})
    df["ticker"] = ticker
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.normalize()
    df["volume"] = df["volume"].fillna(0)
    df = df[COLUMNS].dropna()  # 取引のない日の欠損行は捨てる
    df = df[df["date"] <= last_complete_date(now)].reset_index(drop=True)
    if df.empty:
        raise RuntimeError("確定済みの行がない")
    return df


def fetch_many(tickers: list[str], period: str = "1mo", now: datetime | None = None,
               batch: int = 100) -> dict:
    """銘柄ごとに DataFrame か例外を返す（1銘柄の失敗が他に影響しない）。"""
    import yfinance as yf

    out: dict = {}
    for i in range(0, len(tickers), batch):
        chunk = tickers[i:i + batch]
        try:
            raw = yf.download([yahoo_symbol(t) for t in chunk], period=period, interval="1d",
                              auto_adjust=False, progress=False, group_by="ticker", threads=True)
        except Exception as e:
            out.update({t: e for t in chunk})
            continue
        for t in chunk:
            try:
                sub = raw[yahoo_symbol(t)] if isinstance(raw.columns, pd.MultiIndex) else raw
                out[t] = _clean(sub, t, now)
            except Exception as e:
                out[t] = e
    return out
