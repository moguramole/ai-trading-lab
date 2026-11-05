"""仮想売買エンジン。実注文の経路は存在しない。

約定ルール（固定）: 判断日の「次営業日の始値」に、不利方向のスリッページを加えて約定。
1株単位の仮想約定（単元未満株を仮定）。実際の証券会社の注文方式とは異なる。
"""
from dataclasses import dataclass, field

from .risk import RiskLimits, check_buy, check_sell


@dataclass
class Trade:
    order_id: str
    model: str
    ticker: str
    side: str
    qty: int
    price: float
    fee: float
    trade_date: str
    decided_at: str
    reason: str


@dataclass
class Rejection:
    order_id: str
    reason: str


@dataclass
class PaperBroker:
    model: str
    initial_cash: float = 300_000
    fee_rate: float = 0.001        # 約定代金に対する手数料の仮定
    slippage_rate: float = 0.001   # 不利方向のスリッページの仮定
    limits: RiskLimits = field(default_factory=RiskLimits)
    cash: float = 0.0
    positions: dict = field(default_factory=dict)
    trades: list = field(default_factory=list)
    rejections: list = field(default_factory=list)
    _seen: set = field(default_factory=set)
    _bought: dict = field(default_factory=dict)  # 日付 -> 当日購入額

    def __post_init__(self):
        self.cash = self.initial_cash

    def equity(self, prices):
        """現金 + 保有評価額。pricesは {ticker: 評価価格}。"""
        return self.cash + sum(q * prices[t] for t, q in self.positions.items())

    def submit(self, *, order_id, ticker, side, qty, fill_price, trade_date,
               decided_at="", reason="", mark_prices=None, stop_price=None):
        """注文を判定して約定させる。重複IDは無視。戻り値は Trade か Rejection。"""
        if order_id in self._seen:
            return Rejection(order_id, "重複注文ID")
        self._seen.add(order_id)

        if side not in ("buy", "sell"):
            return self._reject(order_id, "未知の売買区分")
        slip = 1 + self.slippage_rate if side == "buy" else 1 - self.slippage_rate
        price = fill_price * slip
        held = self.positions.get(ticker, 0)

        if side == "buy":
            marks = dict(mark_prices or {})
            marks[ticker] = fill_price
            d = check_buy(
                self.limits, equity=self.equity(marks), cash=self.cash, price=price,
                qty=qty, held_qty=held, bought_today=self._bought.get(trade_date, 0.0),
                stop_price=stop_price,
            )
        else:
            d = check_sell(qty=qty, held_qty=held, price=price)
        if not d.ok:
            return self._reject(order_id, d.reason)

        notional = price * qty
        fee = notional * self.fee_rate
        if side == "buy":
            if notional + fee > self.cash:
                return self._reject(order_id, "現金不足（手数料込み）")
            self.cash -= notional + fee
            self.positions[ticker] = held + qty
            self._bought[trade_date] = self._bought.get(trade_date, 0.0) + notional
        else:
            self.cash += notional - fee
            left = held - qty
            if left:
                self.positions[ticker] = left
            else:
                self.positions.pop(ticker, None)

        t = Trade(order_id, self.model, ticker, side, qty, price, fee,
                  trade_date, decided_at, reason)
        self.trades.append(t)
        return t

    def _reject(self, order_id, reason):
        r = Rejection(order_id, reason)
        self.rejections.append(r)
        return r
