import numpy as np
import pandas as pd
import pytest

from src import backtest, data


def make(n_days=120, n_tickers=12, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2026-01-05", periods=n_days)
    rows = []
    for k in range(n_tickers):
        c = 1000 * np.exp(np.cumsum(rng.normal(0.0005, 0.012, n_days)))
        for i, d in enumerate(dates):
            rows.append((d, f"{1000 + k}", c[i], c[i] * 1.01, c[i] * 0.99, c[i], int(rng.integers(5e5, 2e6))))
    return data.validate(pd.DataFrame(rows, columns=data.COLUMNS))


def test_no_lookahead_signals_use_only_past():
    """最終日の終値を書き換えても、それより前の日付のシグナルは変わらない。"""
    base = make()
    alt = base.copy()
    last = alt["date"].max()
    alt.loc[alt["date"] == last, ["open", "high", "low", "close"]] *= 1.5
    ind1, ind2 = backtest.indicators(base), backtest.indicators(alt)
    for kind in ("pullback", "momentum", "random"):
        s1 = [s for s in backtest.rule_signals(ind1, kind) if s.as_of < f"{last:%Y-%m-%d}"]
        s2 = [s for s in backtest.rule_signals(ind2, kind) if s.as_of < f"{last:%Y-%m-%d}"]
        assert s1 == s2


def test_buys_have_matching_sells_after_hold_days():
    ind = backtest.indicators(make())
    sigs = backtest.rule_signals(ind, "pullback")
    buys = [s for s in sigs if s.action == "buy"]
    assert buys, "テストデータで買いが1件も出ない"
    dates = list(ind["close"].index.strftime("%Y-%m-%d"))
    for b in buys:
        sells = [s for s in sigs if s.action == "sell" and s.ticker == b.ticker and s.qty == b.qty]
        i = dates.index(b.as_of)
        if i + backtest.HOLD_DAYS < len(dates):
            assert any(s.as_of == dates[i + backtest.HOLD_DAYS] for s in sells)


def test_market_filter_only_removes_buys_on_weak_days():
    ind = backtest.indicators(make(n_days=150))
    plain = {s.as_of for s in backtest.rule_signals(ind, "pullback") if s.action == "buy"}
    filt = {s.as_of for s in backtest.rule_signals(ind, "pullback_mkt") if s.action == "buy"}
    assert filt <= plain                                    # フィルターは買いを減らすだけ
    weak = {d.strftime("%Y-%m-%d") for d, b in ind["breadth60"].items() if not b >= backtest.MKT_MIN}
    assert not (filt & weak)                                # 地合いの弱い日には買わない


def test_deep_variant_ranks_by_5day_drop():
    ind = backtest.indicators(make(n_days=150))
    sigs = backtest.rule_signals(ind, "pullback_deep")
    assert sigs and all(s.model == "pullback_deep" for s in sigs)
    for kind in ("pullback_x", "bogus", "pullback_mkt_foo"):
        with pytest.raises(ValueError):
            backtest.rule_signals(ind, kind)


def test_variants_no_lookahead():
    base = make(n_days=150)
    alt = base.copy()
    last = alt["date"].max()
    alt.loc[alt["date"] == last, ["open", "high", "low", "close"]] *= 1.5
    for kind in backtest.VARIANTS:
        s1 = [s for s in backtest.rule_signals(backtest.indicators(base), kind) if s.as_of < f"{last:%Y-%m-%d}"]
        s2 = [s for s in backtest.rule_signals(backtest.indicators(alt), kind) if s.as_of < f"{last:%Y-%m-%d}"]
        assert s1 == s2, kind


def test_random_is_reproducible():
    ind = backtest.indicators(make())
    assert backtest.rule_signals(ind, "random", seed=1) == backtest.rule_signals(ind, "random", seed=1)
    assert backtest.rule_signals(ind, "random", seed=1) != backtest.rule_signals(ind, "random", seed=2)


def test_run_all_shapes_and_split():
    stocks = make()
    bench = stocks[stocks["ticker"] == "1000"].assign(ticker="1306")
    r = backtest.run_all(stocks, bench)
    assert set(backtest.STRATEGIES) | {"random"} <= set(r["curves"])
    assert "TOPIX連動ETF" in r["curves"] and "全銘柄を等金額で保有" in r["curves"]
    lo, hi = r["random_range"]
    assert lo <= r["table"].loc["random", "総リターン"] <= hi
    n = len(stocks["date"].unique()) - backtest.WARMUP
    assert all(len(c) == n for c in r["curves"].values())
    assert {"総リターン", "最大DD", "前半(調整期)", "後半(未知)"} <= set(r["table"].columns)
    # ベンチマークは評価期間の初日に初期資金へ揃う
    assert abs(r["curves"]["TOPIX連動ETF"].iloc[0] - 300_000) < 1e-6
