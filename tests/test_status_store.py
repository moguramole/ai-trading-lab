import pytest
from datetime import datetime

import pandas as pd

from src import data, status, store
from src.ai_adapters import Signal


def test_parse_log_healthy_and_failed():
    ok = ["2026-10-28 16:30:01,100 INFO 7203: 22行保存 (a〜b)"]
    assert status.parse_log(ok)["healthy"]
    bad = ok + ["2026-10-28 16:30:02,100 ERROR 6758: 失敗 boom"]
    r = status.parse_log(bad)
    assert not r["healthy"] and r["last_err"] == datetime(2026, 10, 28, 16, 30, 2)
    # 失敗の後に成功すれば回復扱い
    assert status.parse_log(bad + ["2026-10-29 16:30:01,000 INFO 7203: 1行保存 (a〜b)"])["healthy"]
    assert not status.parse_log([])["healthy"]


def test_business_lag_and_freshness():
    mon, fri = pd.Timestamp("2026-10-26"), pd.Timestamp("2026-10-30")
    assert status.business_lag(fri, fri) == 0
    assert status.business_lag(pd.Timestamp("2026-10-29"), fri) == 1
    assert status.business_lag(mon, fri) == 4
    assert status.freshness(0)[1] == "ok"
    assert status.freshness(1)[1] == "warn"
    assert status.freshness(3)[1] == "bad"


def test_parse_dt_ignores_never_run():
    assert status.parse_dt("1999-11-30T00:00:00") is None
    assert status.parse_dt("2026-10-29T16:30:00") == datetime(2026, 10, 29, 16, 30)


def _accounts():
    import pandas as pd
    from src import sim
    prices = data.validate(pd.DataFrame([
        ("2026-03-02", "1001", 100, 101, 99, 100, 1), ("2026-03-03", "1001", 110, 111, 109, 110, 1),
        ("2026-03-04", "1001", 120, 121, 119, 120, 1)], columns=data.COLUMNS))
    s = Signal("claude", "2026-03-02", "1001", "buy", 0.5, "r", "n", qty=10, stop_price=95)
    bad = Signal("claude", "2026-03-03", "1001", "sell", 0.5, "r", "n", qty=99)  # 保有超過で拒否される
    from src.paper_broker import PaperBroker
    b = PaperBroker("claude", fee_rate=0, slippage_rate=0)
    return {"claude": (b, sim.run(b, prices, [s, bad]))}


def test_ledger_is_append_only_and_idempotent(tmp_path):
    con = data.connect(tmp_path / "t.db")
    acc = _accounts()
    first = store.record_ledger(con, acc)["new_rows"]
    assert first > 0
    assert store.record_ledger(con, acc)["new_rows"] == 0          # 再実行しても増えない
    assert con.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM rejections").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM equity_daily").fetchone()[0] == 3
    assert store.ledger_mismatches(con, acc) == []


def test_ledger_detects_tampering_and_changed_rules(tmp_path):
    con = data.connect(tmp_path / "t.db")
    acc = _accounts()
    store.record_ledger(con, acc)
    con.execute("UPDATE trades SET price = price + 5")             # 記録を書き換える
    con.commit()
    assert store.ledger_mismatches(con, acc) == ["claude|2026-03-02|1001|buy"]
    con.execute("DELETE FROM trades"); con.commit()
    store.record_ledger(con, acc)
    acc["claude"][0].trades.clear()                                # 再計算で約定が消えた
    assert store.ledger_mismatches(con, acc) == ["claude|2026-03-02|1001|buy"]


def test_signal_store_roundtrip_and_dedup(tmp_path):
    con = data.connect(tmp_path / "t.db")
    s = Signal("gpt", "2026-10-28", "7203", "buy", 0.6, "r", "n", qty=5, stop_price=3000.0)
    assert store.save_signals(con, [s]) == 1
    assert store.save_signals(con, [s]) == 0          # 二重登録しない
    assert store.load_signals(con, "gpt") == [s]
    assert store.load_signals(con, "claude") == []


def test_prompt_version_recorded_with_signals(tmp_path):
    from src import prompt
    con = data.connect(tmp_path / "t.db")
    old = Signal("gpt", "2026-10-27", "7203", "hold", 0.5, "r", "n")
    store.save_signals(con, [old])                                   # 版を渡さない既存の経路
    s = Signal("gpt", "2026-10-28", "7203", "buy", 0.6, "r", "n", qty=5, stop_price=3000.0)
    assert store.save_signals(con, [s], prompt.version(), prompt.TEMPLATE) == 1
    summary = {v: n for v, _, n in store.prompt_version_summary(con)}
    assert summary == {None: 1, prompt.version(): 1}                 # 版なしの旧データは分けて数える
    assert con.execute("SELECT template FROM prompt_versions").fetchone()[0] == prompt.TEMPLATE
    store.save_signals(con, [s], prompt.version(), prompt.TEMPLATE)  # 同じ版は二重に保存されない
    assert con.execute("SELECT COUNT(*) FROM prompt_versions").fetchone()[0] == 1


def test_prompt_version_changes_with_template(monkeypatch):
    from src import prompt
    v = prompt.version()
    monkeypatch.setattr(prompt, "TEMPLATE", prompt.TEMPLATE + "【ニュース】")
    assert prompt.version() != v


def test_void_signals_archives_and_blocks_after_fill(tmp_path):
    con = data.connect(tmp_path / "t.db")
    a = Signal("claude", "2026-10-28", "7203", "buy", 0.6, "r", "n", qty=5, stop_price=3000.0)
    b = Signal("gpt", "2026-10-28", "7203", "hold", 0.5, "r", "n")
    store.save_signals(con, [a, b], "v1", "tpl", source="portal")
    assert store.void_signals(con, "claude", "2026-10-28", "ルール変更で出し直し") == 1
    assert store.load_signals(con, "claude") == [] and store.load_signals(con, "gpt") == [b]   # 他は残る
    row = con.execute("SELECT ticker, void_reason, source, prompt_version FROM signals_voided").fetchone()
    assert row == ("7203", "ルール変更で出し直し", "portal", "v1")                              # 消さずに退避
    store.save_signals(con, [a])                                                              # 出し直せる
    assert store.has_signals(con, "claude", "2026-10-28")
    store.ensure_ledger(con)
    con.execute("INSERT INTO trades (order_id, model) VALUES ('claude|2026-10-28|7203|buy', 'claude')")
    with pytest.raises(ValueError):
        store.void_signals(con, "claude", "2026-10-28", "x")                                  # 約定済みは不可

