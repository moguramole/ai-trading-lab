"""成績指標。"""
import pandas as pd


def max_drawdown(equity: pd.Series) -> float:
    """最大ドローダウン（負の比率。例: -0.12）。"""
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    return float(((equity - peak) / peak).min())


def round_trips(trades) -> list[float]:
    """銘柄ごとの平均取得単価で売却時の損益（手数料込み）を返す。"""
    pos: dict[str, tuple[int, float]] = {}
    pnls = []
    for t in trades:
        q, cost = pos.get(t.ticker, (0, 0.0))
        if t.side == "buy":
            pos[t.ticker] = (q + t.qty, cost + t.price * t.qty + t.fee)
        else:
            avg = cost / q
            pnls.append((t.price - avg) * t.qty - t.fee)
            left = q - t.qty
            pos[t.ticker] = (left, avg * left)
    return pnls


def win_rate(trades):
    """勝率と売却回数。回数が少ないうちは参考値。"""
    pnls = round_trips(trades)
    if not pnls:
        return None, 0
    return sum(p > 0 for p in pnls) / len(pnls), len(pnls)
