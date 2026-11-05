from datetime import datetime

import pandas as pd

from src import data, fetch, update

JST = fetch.JST
NOW = datetime(2026, 10, 27, 16, 0, tzinfo=JST)  # テスト用の「現在」（2026-10-27の取引終了後）


def test_last_complete_date_excludes_today_before_close():
    assert fetch.last_complete_date(datetime(2026, 10, 28, 12, 0, tzinfo=JST)) == pd.Timestamp("2026-10-27")
    assert fetch.last_complete_date(datetime(2026, 10, 28, 16, 0, tzinfo=JST)) == pd.Timestamp("2026-10-28")


def test_watchlist_ignores_comments(tmp_path):
    p = tmp_path / "w.txt"
    p.write_text("# c\n7203  # toyota\n\n6758\n", encoding="utf-8")
    assert fetch.read_watchlist(p) == ["7203", "6758"]


def test_yahoo_symbol():
    assert fetch.yahoo_symbol("7203") == "7203.T"
    assert fetch.yahoo_symbol("^N225") == "^N225"


def test_held_stock_outside_universe_is_fetched_but_not_traded(tmp_path):
    from src import portfolio, runner, universe
    con = data.connect(tmp_path / "t.db")
    universe.ensure(con)
    con.execute("INSERT INTO universe VALUES ('1001','A','電気機器','TOPIX Core30','2026-10-28T00:00:00+00:00')")
    con.commit()
    portfolio.upsert(con, "9999", 10, 1000)            # TOPIX500外の保有
    tickers = update.target_tickers(con, watchlist=tmp_path / "none.txt")
    assert "9999" in tickers and "1001" in tickers and "^N225" in tickers   # 価格は取得する
    prices = pd.DataFrame([("2026-10-27", t, 100, 105, 99, 102, 1000) for t in ("1001", "9999", "^N225")],
                          columns=data.COLUMNS).assign(date=lambda d: pd.to_datetime(d["date"]))
    stocks, bench = runner.split_benchmarks(prices, portfolio.extra_tickers(con, universe.load(con)))
    assert list(stocks["ticker"]) == ["1001"] and list(bench["ticker"]) == ["^N225"]   # 売買対象には入れない


def _ok(t):
    return pd.DataFrame([("2026-10-27", t, 100, 105, 99, 102, 1000)], columns=data.COLUMNS)


def test_update_is_idempotent_and_isolates_failures(tmp_path):
    db = tmp_path / "t.db"
    tickers = [f"{1000 + i}" for i in range(10)]

    def fetcher(ts, period):
        return {t: (RuntimeError("boom") if t == "1001" else _ok(t)) for t in ts}

    assert update.run(db_path=db, fetcher=fetcher, tickers=tickers, now=NOW) == 1   # 10銘柄中1失敗=10% > 5%
    assert update.run(db_path=db, fetcher=fetcher, tickers=tickers, now=NOW) == 1   # 再実行
    got = data.read_prices(data.connect(db))
    assert len(got) == 9 and "1001" not in set(got["ticker"])             # 成功分のみ・重複なし


def test_small_failure_ratio_is_tolerated(tmp_path):
    tickers = [f"{1000 + i}" for i in range(100)]
    fetcher = lambda ts, p: {t: (RuntimeError("x") if t == "1000" else _ok(t)) for t in ts}
    assert update.run(db_path=tmp_path / "t.db", fetcher=fetcher, tickers=tickers, now=NOW) == 0  # 1% は許容


def test_bad_data_not_saved(tmp_path):
    bad = lambda ts, p: {t: pd.DataFrame([("2026-10-27", t, 0, 105, 99, 102, 1000)],
                                         columns=data.COLUMNS) for t in ts}
    assert update.run(db_path=tmp_path / "t.db", fetcher=bad, tickers=["1001"]) == 1
    assert data.read_prices(data.connect(tmp_path / "t.db")).empty


def test_alert_written_on_failure_and_cleared_on_success(tmp_path):
    from src import alert
    db, path = tmp_path / "t.db", tmp_path / "alert.json"
    tickers = [f"{1000 + i}" for i in range(10)]
    sent = []
    bad = lambda ts, p: {t: (RuntimeError("boom") if t == "1001" else _ok(t)) for t in ts}
    assert update.run(db_path=db, fetcher=bad, tickers=tickers, now=NOW, notifier=lambda *a: sent.append(a)) == 1
    a = alert.read(path)
    assert a and "1001" in a["detail"][0] and len(sent) == 1
    good = lambda ts, p: {t: _ok(t) for t in ts}
    assert update.run(db_path=db, fetcher=good, tickers=tickers, now=NOW, notifier=lambda *a: sent.append(a)) == 0
    assert alert.read(path) is None and len(sent) == 1       # 回復すれば消え、通知も増えない


def test_stale_data_raises_alert_even_if_fetch_succeeds(tmp_path):
    from src import alert
    later = datetime(2026, 10, 29, 16, 0, tzinfo=JST)         # 取得元が止まり、日付が進まない
    good = lambda ts, p: {t: _ok(t) for t in ts}
    assert update.run(db_path=tmp_path / "t.db", fetcher=good, tickers=["1001"], now=later) == 1
    assert "遅れ" in alert.read(tmp_path / "alert.json")["detail"][0]


def test_crash_is_reported_not_swallowed(tmp_path):
    from src import alert
    def boom(ts, p):
        raise RuntimeError("network down")
    assert update.run(db_path=tmp_path / "t.db", fetcher=boom, tickers=["1001"], now=NOW) == 1
    assert "network down" in alert.read(tmp_path / "alert.json")["detail"][0]
