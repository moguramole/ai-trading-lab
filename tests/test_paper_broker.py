import pytest

from src.paper_broker import PaperBroker, Rejection, Trade


def mk(**kw):
    return PaperBroker("claude", fee_rate=0, slippage_rate=0, **kw)


def buy(b, oid="o1", qty=10, price=1000, day="2026-03-03", **kw):
    return b.submit(order_id=oid, ticker="1001", side="buy", qty=qty,
                    fill_price=price, trade_date=day, **kw)


def test_buy_hold_sell_equity_matches():
    b = mk()
    assert isinstance(buy(b), Trade)
    assert b.cash == 290_000 and b.positions == {"1001": 10}
    assert b.equity({"1001": 1100}) == 290_000 + 11_000
    b.submit(order_id="o2", ticker="1001", side="sell", qty=10, fill_price=1100,
             trade_date="2026-03-10")
    assert b.positions == {} and b.cash == 301_000
    assert b.equity({}) == 301_000


def test_duplicate_order_id_ignored():
    b = mk()
    buy(b)
    r = buy(b)
    assert isinstance(r, Rejection) and "重複" in r.reason
    assert b.positions == {"1001": 10}


def test_insufficient_cash_rejected():
    b = mk(initial_cash=5_000)
    assert isinstance(buy(b), Rejection)
    assert b.cash == 5_000


def test_short_sell_rejected():
    b = mk()
    r = b.submit(order_id="s", ticker="1001", side="sell", qty=1, fill_price=1000,
                 trade_date="2026-03-03")
    assert isinstance(r, Rejection)


def test_zero_price_rejected():
    assert isinstance(buy(mk(), price=0), Rejection)


def test_unknown_side_rejected():
    r = mk().submit(order_id="x", ticker="1001", side="short", qty=1, fill_price=1000,
                    trade_date="2026-03-03")
    assert isinstance(r, Rejection)


def test_fee_and_slippage_applied():
    b = PaperBroker("gpt", fee_rate=0.001, slippage_rate=0.001)
    t = buy(b)
    assert t.price == pytest.approx(1001)
    assert t.fee == pytest.approx(10.01)
    assert b.cash == pytest.approx(300_000 - 10_010 - 10.01)


def test_accounts_are_independent():
    g, c = mk(), mk()
    buy(g)
    assert c.positions == {} and c.cash == 300_000
