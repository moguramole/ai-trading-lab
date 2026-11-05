"""個人の持ち株の記録と評価。

仮想売買の口座とは完全に別。AIの判断・仮想約定・リスク判定には一切使わない（注文機能もない）。
評価は取得済みの終値ベースで、証券会社の画面とは一致しない（約定コスト・配当・為替は含めない）。
"""
from datetime import datetime, timezone

import pandas as pd


class InvalidHolding(ValueError):
    pass


def ensure(con):
    con.execute("""CREATE TABLE IF NOT EXISTS holdings (
        ticker TEXT PRIMARY KEY, qty INTEGER NOT NULL, avg_cost REAL NOT NULL, updated_at TEXT)""")


def validate(ticker, qty, avg_cost):
    ticker = str(ticker).strip().upper()
    if not ticker:
        raise InvalidHolding("銘柄コードが空です")
    if isinstance(qty, bool) or int(qty) != qty or qty <= 0:
        raise InvalidHolding("株数は1以上の整数にしてください")
    if not avg_cost or avg_cost <= 0:
        raise InvalidHolding("平均取得単価は正の数にしてください")
    return ticker, int(qty), float(avg_cost)


def upsert(con, ticker, qty, avg_cost):
    """追加または更新（同じ銘柄コードは上書き）。"""
    ensure(con)
    t, q, c = validate(ticker, qty, avg_cost)
    con.execute("INSERT OR REPLACE INTO holdings VALUES (?,?,?,?)",
                (t, q, c, datetime.now(timezone.utc).isoformat()))
    con.commit()


def remove(con, ticker):
    ensure(con)
    con.execute("DELETE FROM holdings WHERE ticker=?", (str(ticker).strip().upper(),))
    con.commit()


def load(con) -> pd.DataFrame:
    ensure(con)
    return pd.read_sql_query("SELECT ticker,qty,avg_cost FROM holdings ORDER BY ticker", con)


def extra_tickers(con, universe: pd.DataFrame) -> set[str]:
    """持ち株のうち、仮想売買の対象（TOPIX500）に含まれない銘柄。価格は取得するが売買対象にはしない。"""
    if universe.empty:
        return set()
    return set(load(con)["ticker"]) - set(universe["ticker"])


def valuation(holdings: pd.DataFrame, snap: pd.DataFrame) -> pd.DataFrame:
    """保有に最新の終値・騰落・業種を付け、評価額と損益を計算する。

    snapは market.snapshot の結果。価格が無い銘柄（TOPIX500外など）は価格・損益を欠損にし、
    合計にも含めない（取得原価だけ別に出せるよう cost 列は残す）。
    """
    if holdings.empty:
        return holdings.assign(name="", sector33="", close=0.0, cost=0.0, value=0.0, pnl=0.0,
                               pnl_pct=0.0, weight=0.0, ret1=0.0, ret20=0.0)
    cols = [c for c in ("ticker", "name", "sector33", "close", "ret1", "ret20") if c in snap]
    df = holdings.merge(snap[cols], on="ticker", how="left")
    for c in ("name", "sector33"):
        if c not in df:
            df[c] = ""
        df[c] = df[c].fillna("")
    df["cost"] = df["qty"] * df["avg_cost"]
    df["value"] = df["qty"] * df["close"]
    df["pnl"] = df["value"] - df["cost"]
    df["pnl_pct"] = df["close"] / df["avg_cost"] - 1
    df["weight"] = df["value"] / df["value"].sum()
    return df


def totals(df: pd.DataFrame) -> dict:
    """価格のある銘柄だけの合計。"""
    priced = df.dropna(subset=["close"])
    cost, value = priced["cost"].sum(), priced["value"].sum()
    return {"cost": cost, "value": value, "pnl": value - cost,
            "pnl_pct": (value / cost - 1) if cost else 0.0,
            "n": len(df), "unpriced": len(df) - len(priced)}
