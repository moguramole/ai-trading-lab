from src.risk import RiskLimits, check_buy, check_sell

L = RiskLimits()
BASE = dict(equity=300_000, cash=300_000, price=1000, qty=10, held_qty=0, bought_today=0)


def test_ok_buy():
    assert check_buy(L, **BASE).ok


def test_insufficient_cash():
    assert not check_buy(L, **{**BASE, "cash": 5_000}).ok


def test_position_limit():
    # 20%=60,000円。 保有0で 70株×1000円=70,000円
    d = check_buy(L, **{**BASE, "qty": 70, "stop_price": 999})
    assert not d.ok and "配分" in d.reason


def test_daily_buy_limit():
    d = check_buy(L, **{**BASE, "bought_today": 55_000, "stop_price": 999})
    assert not d.ok and "1日" in d.reason


def test_loss_limit():
    # 損切り幅5%×10万円=5,000円 > 資金の0.5%(1,500円)
    d = check_buy(L, **{**BASE, "qty": 100})
    assert not d.ok


def test_stop_must_be_below_price():
    assert not check_buy(L, **{**BASE, "stop_price": 1000}).ok


def test_bad_price_or_qty():
    assert not check_buy(L, **{**BASE, "price": 0}).ok
    assert not check_buy(L, **{**BASE, "qty": 0}).ok


def test_no_short_selling():
    assert not check_sell(qty=5, held_qty=0, price=1000).ok
    assert check_sell(qty=5, held_qty=5, price=1000).ok
    assert not check_sell(qty=5, held_qty=5, price=0).ok
