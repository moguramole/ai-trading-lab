"""AIなしの比較用ベースライン戦略（移動平均クロス）。判断日までのデータだけを使う。"""
import pandas as pd


def sma_cross_action(closes: pd.Series, fast=5, slow=20) -> str:
    """closesは判断日までの終値。buy / sell / hold を返す。"""
    if len(closes) < slow + 1:
        return "hold"
    f = closes.rolling(fast).mean()
    s = closes.rolling(slow).mean()
    if f.iloc[-2] <= s.iloc[-2] and f.iloc[-1] > s.iloc[-1]:
        return "buy"
    if f.iloc[-2] >= s.iloc[-2] and f.iloc[-1] < s.iloc[-1]:
        return "sell"
    return "hold"


def sma_cross_actions(closes: pd.Series, fast=5, slow=20) -> pd.Series:
    """全期間を一括計算した版（銘柄数が多いとき用）。各日の値は sma_cross_action と一致する。"""
    f, s = closes.rolling(fast).mean(), closes.rolling(slow).mean()
    up = (f.shift() <= s.shift()) & (f > s)
    down = (f.shift() >= s.shift()) & (f < s)
    out = pd.Series("hold", index=closes.index)
    out[up] = "buy"
    out[down] = "sell"
    out.iloc[:slow] = "hold"  # 20本未満は判断しない
    return out
