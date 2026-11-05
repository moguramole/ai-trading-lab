"""資金・リスク制限の判定。Pythonが最終判断者で、AIの出力は提案に過ぎない。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class RiskLimits:
    max_position_pct: float = 0.20      # 1銘柄への配分上限
    max_loss_per_trade_pct: float = 0.005  # 1回の想定損失上限
    max_daily_buy_pct: float = 0.20     # 1日の新規購入額上限
    default_stop_pct: float = 0.05      # 損切り価格が未指定のときの仮定幅
    allow_short: bool = False           # 空売り禁止（固定）


@dataclass(frozen=True)
class Decision:
    ok: bool
    reason: str = ""


def check_buy(limits, *, equity, cash, price, qty, held_qty, bought_today, stop_price=None):
    if qty <= 0 or price <= 0:
        return Decision(False, "数量または価格が不正")
    cost = price * qty
    if cost > cash:
        return Decision(False, "現金不足")
    if (held_qty * price + cost) > equity * limits.max_position_pct:
        return Decision(False, "1銘柄の配分上限超過")
    if bought_today + cost > equity * limits.max_daily_buy_pct:
        return Decision(False, "1日の新規購入額上限超過")
    stop = stop_price if stop_price else price * (1 - limits.default_stop_pct)
    if stop >= price:
        return Decision(False, "損切り価格が約定価格以上")
    if (price - stop) * qty > equity * limits.max_loss_per_trade_pct:
        return Decision(False, "1回の想定損失上限超過")
    return Decision(True)


def check_sell(*, qty, held_qty, price):
    if price <= 0:
        return Decision(False, "価格が不正")
    if qty <= 0:
        return Decision(False, "数量が不正")
    if qty > held_qty:
        return Decision(False, "保有数超過（空売り禁止）")
    return Decision(True)
