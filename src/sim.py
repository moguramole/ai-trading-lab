"""シグナルを日付順に仮想約定させ、日次の資産推移を返す。

未来情報の混入防止: 判断日(as_of)の終値までで判断し、約定は次営業日の始値。
"""
import pandas as pd

from .ai_adapters import Signal
from .paper_broker import PaperBroker


def order_id(s: Signal) -> str:
    return f"{s.model}|{s.as_of}|{s.ticker}|{s.action}"


def run(broker: PaperBroker, prices: pd.DataFrame, signals: list[Signal]) -> pd.DataFrame:
    """日次の資産推移 DataFrame(date, equity) を返す。holdは何もしない。"""
    by_date: dict = {}
    for s in signals:
        by_date.setdefault(pd.Timestamp(s.as_of), []).append(s)

    px = prices.set_index(["date", "ticker"])
    dates = sorted(prices["date"].unique())
    last_close: dict[str, float] = {}
    curve = []
    pending: list[Signal] = []

    for d in dates:
        today = px.loc[d]
        # 前営業日に出た判断を、今日の始値で約定
        for s in pending:
            if s.ticker not in today.index:
                broker.rejections.append(_rej(s, "約定日に価格なし"))
                continue
            broker.submit(
                order_id=order_id(s), ticker=s.ticker, side=s.action, qty=s.qty,
                fill_price=float(today.loc[s.ticker, "open"]),
                trade_date=pd.Timestamp(d).strftime("%Y-%m-%d"),
                decided_at=s.as_of, reason=s.reason,
                mark_prices=last_close, stop_price=s.stop_price,
            )
        pending = [s for s in by_date.get(pd.Timestamp(d), []) if s.action != "hold"]
        for t in today.index:
            last_close[t] = float(today.loc[t, "close"])
        curve.append({"date": d, "equity": broker.equity(last_close)})
    return pd.DataFrame(curve)


def _rej(s, reason):
    from .paper_broker import Rejection
    return Rejection(order_id(s), reason)
