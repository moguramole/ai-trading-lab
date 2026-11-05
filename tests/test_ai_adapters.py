import json
import pandas as pd

import pytest

from src.ai_adapters import InvalidSignal, parse_signals

GOOD = dict(model="claude", as_of="2026-03-02", ticker="1001", action="buy",
            confidence=0.6, reason="r", risk_notes="n", qty=10, stop_price=1400)


def j(**kw):
    return json.dumps({**GOOD, **kw})


def test_valid():
    (s,) = parse_signals(j(), expected_model="claude", known_tickers={"1001"})
    assert s.action == "buy" and s.qty == 10


def test_list_ok():
    assert len(parse_signals(json.dumps([GOOD, {**GOOD, "ticker": "1002"}]))) == 2


@pytest.mark.parametrize("text", ["not json", "[]", "123", "null"])
def test_garbage(text):
    with pytest.raises(InvalidSignal):
        parse_signals(text)


def test_missing_field():
    d = {k: v for k, v in GOOD.items() if k != "reason"}
    with pytest.raises(InvalidSignal):
        parse_signals(json.dumps(d))


def test_unknown_action():
    with pytest.raises(InvalidSignal):
        parse_signals(j(action="short"))


def test_model_mismatch():
    with pytest.raises(InvalidSignal):
        parse_signals(j(), expected_model="gpt")


def test_unknown_ticker():
    with pytest.raises(InvalidSignal):
        parse_signals(j(ticker="9999"), known_tickers={"1001"})


@pytest.mark.parametrize("conf", [-0.1, 1.5, "high", True])
def test_bad_confidence(conf):
    with pytest.raises(InvalidSignal):
        parse_signals(j(confidence=conf))


def test_trade_needs_qty():
    with pytest.raises(InvalidSignal):
        parse_signals(j(qty=0))


def test_hold_without_qty_ok():
    (s,) = parse_signals(j(action="hold", qty=0))
    assert s.action == "hold"


# ---- 登録の締め切りガード -----------------------------------------------------
from datetime import datetime  # noqa: E402

from src.ai_adapters import JST, Signal, check_deadline  # noqa: E402

SIG = Signal("claude", "2026-10-28", "1001", "buy", 0.5, "r", "n", qty=1)  # 水曜の判断
LATEST = datetime(2026, 10, 28).date()


def at(s):
    return datetime.fromisoformat(s).replace(tzinfo=JST)


def test_deadline_ok_before_next_open():
    check_deadline([SIG], LATEST, now=at("2026-10-28T20:00"))
    check_deadline([SIG], LATEST, now=at("2026-10-29T08:59"))


def test_deadline_rejects_after_next_open():
    with pytest.raises(InvalidSignal):
        check_deadline([SIG], LATEST, now=at("2026-10-29T09:00"))


def test_deadline_rejects_when_newer_prices_exist():
    with pytest.raises(InvalidSignal):
        check_deadline([SIG], datetime(2026, 10, 29).date(), now=at("2026-10-28T20:00"))


def test_deadline_friday_decision_open_until_monday():
    fri = Signal("claude", "2026-10-30", "1001", "buy", 0.5, "r", "n", qty=1)
    check_deadline([fri], datetime(2026, 10, 30).date(), now=at("2026-11-01T12:00"))
    with pytest.raises(InvalidSignal):
        check_deadline([fri], datetime(2026, 10, 30).date(), now=at("2026-11-02T09:00"))


def test_deadline_one_late_signal_rejects_all():
    old = Signal("claude", "2026-10-27", "1001", "hold", 0.5, "r", "n")
    with pytest.raises(InvalidSignal):
        check_deadline([SIG, old], LATEST, now=at("2026-10-28T20:00"))


# ---- 営業日カレンダー ---------------------------------------------------------
from datetime import date  # noqa: E402

from src import jpcal  # noqa: E402


def test_holidays_and_new_year_closure():
    assert not jpcal.is_business_day(date(2026, 11, 3))     # 文化の日
    assert not jpcal.is_business_day(date(2026, 9, 22))     # 国民の休日
    assert not jpcal.is_business_day(date(2026, 12, 31)) and not jpcal.is_business_day(date(2027, 1, 2))
    assert jpcal.is_business_day(date(2026, 10, 28))
    assert jpcal.is_business_day(date(2026, 12, 30))        # 大納会


def test_next_and_last_business_day():
    assert jpcal.next_business_day(date(2026, 11, 2)) == date(2026, 11, 4)    # 11/3は祝日
    assert jpcal.next_business_day(date(2026, 12, 30)) == date(2027, 1, 4)    # 年末年始
    assert jpcal.last_business_day(date(2026, 11, 3)) == date(2026, 11, 2)
    assert jpcal.last_business_day(date(2026, 10, 28)) == date(2026, 10, 28)


def test_deadline_extends_over_holiday():
    mon = Signal("claude", "2026-11-02", "1001", "buy", 0.5, "r", "n", qty=1)
    d = date(2026, 11, 2)
    check_deadline([mon], d, now=at("2026-11-04T08:59"))     # 火曜(祝日)を飛ばして水曜9:00まで
    with pytest.raises(InvalidSignal):
        check_deadline([mon], d, now=at("2026-11-04T09:00"))


def test_lag_ignores_holidays():
    from src import fetch, status
    assert status.business_lag(pd.Timestamp("2026-11-02"), pd.Timestamp("2026-11-04")) == 1   # 11/3は数えない
    assert status.business_lag(pd.Timestamp("2026-11-02"), pd.Timestamp("2026-11-03")) == 0
    # 祝日の夕方は、直前の営業日が「最新であるべき日」
    assert fetch.last_complete_date(datetime(2026, 11, 3, 16, 0, tzinfo=JST)) == pd.Timestamp("2026-11-02")



def test_deadline_accepts_pandas_timestamp():
    # ポータルは prices["date"].max()（pandas.Timestamp）を渡す
    check_deadline([SIG], pd.Timestamp("2026-10-28"), now=at("2026-10-28T20:00"))
    with pytest.raises(InvalidSignal):
        check_deadline([SIG], pd.Timestamp("2026-10-29"), now=at("2026-10-28T20:00"))


# ---- 情報の締め切り（21:30） ---------------------------------------------------
from src import schedule  # noqa: E402
from src.ai_adapters import check_not_early  # noqa: E402


def test_not_early_boundary():
    check_not_early([SIG], now=at("2026-10-28T21:30"))
    check_not_early([SIG], now=at("2026-10-29T08:00"))
    with pytest.raises(InvalidSignal):
        check_not_early([SIG], now=at("2026-10-28T21:29"))
    with pytest.raises(InvalidSignal):
        check_not_early([SIG], now=at("2026-10-28T16:30"))


def test_expected_latest_switches_at_run_time():
    assert schedule.expected_latest(at("2026-10-28T21:29")) == pd.Timestamp("2026-10-27")
    assert schedule.expected_latest(at("2026-10-28T21:30")) == pd.Timestamp("2026-10-28")
    assert schedule.expected_latest(at("2026-11-03T22:00")) == pd.Timestamp("2026-11-02")   # 祝日は直前の営業日
