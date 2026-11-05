from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from src import data, market, prompt, signals, universe


def make_prices(spec, days=80):
    """spec: {ticker: (日次ドリフト, 最終5日の追加ドリフト)} の架空データ。"""
    rows, dates = [], pd.bdate_range("2026-06-01", periods=days)
    for t, (drift, tail) in spec.items():
        c = 1000.0
        for i, d in enumerate(dates):
            c *= 1 + drift + (tail if i >= days - 5 else 0)
            rows.append((d, t, c, c * 1.01, c * 0.99, c, 1_000_000))
    return pd.DataFrame(rows, columns=data.COLUMNS)


def test_snapshot_and_breadth():
    snap = market.snapshot(make_prices({"1001": (0.002, 0), "1002": (-0.002, 0)}))
    assert set(snap["ticker"]) == {"1001", "1002"}
    up = snap.set_index("ticker").loc["1001"]
    assert up["trend_up"] and up["above60"] and up["ret20"] > 0
    b = market.breadth(snap)
    assert b["銘柄数"] == 2 and b["本日上昇"] == 0.5


def test_short_history_skipped():
    assert market.snapshot(make_prices({"1001": (0.001, 0)}, days=10)).empty


def test_candidates_pullback_and_holdings():
    # 1001: 上昇基調で直近5日だけ下落=押し目 / 1002: 下落基調 / 1003: 保有中
    snap = market.snapshot(make_prices({"1001": (0.003, -0.006), "1002": (-0.002, 0), "1003": (-0.001, 0)}))
    c = market.candidates(snap, held=["1003"])
    reasons = dict(zip(c["ticker"], c["reason"]))
    assert reasons["1001"] == "上昇基調の押し目"
    assert reasons["1003"] == "保有中"
    assert "1002" not in reasons


def test_candidates_excludes_expensive_stock():
    p = make_prices({"1001": (0.003, -0.006)})
    p[["open", "high", "low", "close"]] *= 100  # 単価10万円超
    assert "1001" not in set(market.candidates(market.snapshot(p))["ticker"])


def test_vectorized_sma_matches_loop():
    rng = np.random.default_rng(1)
    c = pd.Series(1000 * np.exp(np.cumsum(rng.normal(0, 0.02, 120))))
    vec = signals.sma_cross_actions(c)
    loop = pd.Series([signals.sma_cross_action(c.iloc[: i + 1]) for i in range(len(c))])
    assert (vec == loop).all()


RAW = pd.DataFrame({
    "コード": ["7203", "285A", "1301", "9999"], "銘柄名": ["トヨタ", "キオクシア", "極洋", "ETF"],
    "33業種区分": ["輸送用機器", "電気機器", "水産・農林業", "-"],
    "規模区分": ["TOPIX Core30", "TOPIX Mid400", "TOPIX Small 2", "-"]})


def test_universe_select_topix500_only():
    u = universe.select(RAW)
    assert list(u["ticker"]) == ["7203", "285A"]


def test_universe_refresh_uses_cache_then_fallback(tmp_path):
    con = data.connect(tmp_path / "t.db")
    now = datetime(2026, 10, 28, tzinfo=timezone.utc)
    u, msg = universe.refresh(con, now, fetcher=lambda: universe.select(RAW))
    assert len(u) == 2 and "更新" in msg
    # 7日以内は再取得しない
    _, msg = universe.refresh(con, now + timedelta(days=1), fetcher=lambda: 1 / 0)
    assert "保存済み" in msg
    # 期限切れで取得に失敗しても保存分で続行
    u, msg = universe.refresh(con, now + timedelta(days=8), fetcher=lambda: 1 / 0)
    assert len(u) == 2 and "失敗" in msg


def test_momentum_not_in_default_candidates_but_available_on_request():
    # 20日高値を更新し出来高も急増する銘柄を作る
    p = make_prices({"1001": (0.003, 0), "1002": (0.0, 0)})
    last = p["date"].max()
    p.loc[(p.ticker == "1001") & (p.date == last), ["close", "high", "open", "low"]] *= 1.2
    p.loc[(p.ticker == "1001") & (p.date == last), "volume"] *= 3
    snap = market.snapshot(p)
    assert "高値更新+出来高増" not in set(market.candidates(snap)["reason"])
    assert "高値更新+出来高増" in set(market.candidates(snap, n_momentum=8)["reason"])


def _prompt(spec):
    snap = market.snapshot(make_prices(spec))
    idx = pd.DataFrame([{"指標": "日経平均", "値": 50000.0, "本日": 0.01, "五日": 0.02, "二十日": 0.03}])
    return prompt.build_prompt(model="gpt", as_of="2026-10-28", initial=300000, equity=300000, cash=300000,
                               positions={}, index_df=idx, breadth=market.breadth(snap),
                               sectors=market.sector_table(snap), cands=market.candidates(snap))


def test_prompt_warns_only_when_market_is_weak():
    weak = _prompt({"1001": (-0.002, 0), "1002": (-0.002, 0), "1003": (-0.002, 0)})   # 全銘柄が60日線の下
    strong = _prompt({"1001": (0.003, 0), "1002": (0.003, 0), "1003": (0.003, 0)})
    assert "【地合いの注意】" in weak and "確実ではありません" in weak
    assert "【地合いの注意】" not in strong


def test_build_prompt_contains_key_parts():
    snap = market.snapshot(make_prices({"1001": (0.003, -0.006)}))
    cands = market.candidates(snap)
    idx = pd.DataFrame([{"指標": "日経平均", "値": 50000.0, "本日": 0.01, "五日": 0.02, "二十日": 0.03}])
    text = prompt.build_prompt(model="gpt", as_of="2026-10-28", initial=300000, equity=300000, cash=300000,
                               positions={}, index_df=idx, breadth=market.breadth(snap),
                               sectors=market.sector_table(snap), cands=cands)
    for s in ["gpt", "2026-10-28", "日経平均", "1001", "保有: なし", "空売り禁止"]:
        assert s in text


# ---- AIが一覧から銘柄を選ぶ依頼文（V2） --------------------------------------------
def test_screened_merges_reasons_and_is_deterministic():
    snap = market.snapshot(data.load_csv("sample/sample_prices.csv"))
    s = market.screened(snap)
    assert s["ticker"].is_unique and len(s) <= 80
    assert "上昇基調の押し目" in s.loc[s["ticker"] == "1001", "reason"].iloc[0]
    assert "/" in s["reason"].iloc[0]                                    # 複数の条件に該当した銘柄は条件をまとめる
    assert s.equals(market.screened(snap))


def test_screened_puts_held_first_even_if_not_screened():
    snap = market.snapshot(data.load_csv("sample/sample_prices.csv"))
    s = market.screened(snap, held=["1003"])                             # 抽出には入らない銘柄を保有中
    assert s["ticker"].iloc[0] == "1003" and s["reason"].iloc[0] == "保有中"


def test_prompt_version_is_gated_by_decision_date():
    assert prompt.version("2026-10-28") == "615be298"                    # 登録済みの10/28分と同じ版
    assert prompt.version("2026-10-29") != prompt.version("2026-10-28")
    assert "一覧にない銘柄は出力しないでください" in prompt.template("2026-10-29")
    assert "一覧にない銘柄" not in prompt.template("2026-10-28")
    assert prompt.template() == prompt.template("2026-10-29")
