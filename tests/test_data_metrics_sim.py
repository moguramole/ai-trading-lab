import pandas as pd
import pytest

from src import data, metrics, sim
from src.ai_adapters import Signal
from src.paper_broker import PaperBroker


def frame(rows):
    return pd.DataFrame(rows, columns=data.COLUMNS)


OK = ("2026-03-02", "1001", 100, 105, 99, 102, 1000)


def test_validate_ok():
    assert len(data.validate(frame([OK]))) == 1


@pytest.mark.parametrize("bad", [
    ("2026-03-02", "1001", None, 105, 99, 102, 1000),   # 欠損
    ("2026-03-02", "1001", 0, 105, 99, 102, 1000),      # 価格0
    ("2026-03-02", "1001", 100, 98, 99, 102, 1000),     # 高値<終値
    ("2026-03-02", "1001", 100, 105, 99, 102, -1),      # 出来高負
])
def test_validate_rejects(bad):
    with pytest.raises(data.DataError):
        data.validate(frame([bad]))


def test_validate_duplicates():
    with pytest.raises(data.DataError):
        data.validate(frame([OK, OK]))


def test_sqlite_roundtrip(tmp_path):
    con = data.connect(tmp_path / "t.db")
    data.save_prices(con, data.validate(frame([OK])), "test")
    data.save_prices(con, data.validate(frame([OK])), "test")  # 再実行しても重複しない
    assert len(data.read_prices(con)) == 1


def series(ticker, closes, vol=1000):
    d = pd.bdate_range("2026-03-02", periods=len(closes))
    return frame([(x, ticker, c, c * 1.01, c * 0.99, c, vol) for x, c in zip(d, closes)])


def test_clean_normal_data_untouched():
    df = series("1001", [100, 101, 99, 102, 103, 104])
    out, flags = data.clean(df)
    assert flags.empty and out["close"].tolist() == df["close"].tolist()


def test_clean_removes_transient_spike_block():
    # 2日だけ約1/10の異常値(例: 1306で実際に起きた)
    out, flags = data.clean(series("1306", [380, 382, 38, 37, 383, 385, 386]))
    assert out["close"].tolist() == [380, 382, 383, 385, 386]
    assert list(flags["kind"]) == ["異常値を除去"]


def test_clean_removes_garbage_row_before_listing():
    out, flags = data.clean(series("8303", [1700, 1710, 5.5e10, 1800, 1731, 1721]))
    assert 5.5e10 not in out["close"].tolist() and len(out) == 5


def test_clean_drops_absurd_prices_before_listing():
    # 再上場前の期間がまるごと無効な巨大価格（8303で実際に起きた）
    out, flags = data.clean(series("8303", [5.5e10, 5.5e10, 5.5e10, 1800, 1731, 1721, 1681]))
    assert out["close"].tolist() == [1800, 1731, 1721, 1681]
    assert flags.iloc[0]["kind"] == "無効な価格を除去" and flags.iloc[0]["detail"] == "3行"


def test_clean_adjusts_split_prices_and_volume():
    out, flags = data.clean(series("8227", [11000, 11040, 3713, 3628, 3537]))
    assert flags.iloc[0]["kind"] == "分割調整"
    assert out["close"].iloc[0] == 11000 / 3 and out["volume"].iloc[0] == 3000   # 過去分を調整
    assert out["close"].iloc[-1] == 3537                                          # 以降は不変
    assert (out["close"].pct_change().abs().dropna() < data.JUMP).all()


def test_clean_reverse_split():
    out, flags = data.clean(series("1001", [100, 101, 1000, 1010]))
    assert flags.iloc[0]["detail"].startswith("併合") and out["close"].iloc[0] == 1000.0


def test_clean_excludes_unexplained_shift():
    out, flags = data.clean(series("1001", [100, 101, 160, 161, 162]))   # 1.6倍の水準ずれ
    assert out.empty and flags.iloc[0]["kind"] == "銘柄を除外"


def test_clean_is_per_ticker():
    df = pd.concat([series("1001", [100, 101, 102, 103]), series("1002", [100, 101, 30, 30])])
    out, flags = data.clean(df)
    assert set(out["ticker"]) == {"1001"} and list(flags["ticker"]) == ["1002"]


def test_max_drawdown():
    assert metrics.max_drawdown(pd.Series([100, 120, 90, 110])) == pytest.approx(-0.25)
    assert metrics.max_drawdown(pd.Series([], dtype=float)) == 0.0


def test_sim_fills_next_open_and_is_idempotent():
    prices = data.validate(frame([
        ("2026-03-02", "1001", 100, 101, 99, 100, 1),
        ("2026-03-03", "1001", 110, 111, 109, 110, 1),
        ("2026-03-04", "1001", 120, 121, 119, 120, 1),
    ]))
    sig = Signal("claude", "2026-03-02", "1001", "buy", 0.5, "r", "n", qty=10, stop_price=95)
    b = PaperBroker("claude", fee_rate=0, slippage_rate=0)
    curve = sim.run(b, prices, [sig, sig])
    assert b.trades[0].price == 110          # 判断翌日の始値
    assert len(b.trades) == 1                # 同一注文IDは二重約定しない
    assert curve["equity"].iloc[-1] == pytest.approx(300_000 + 10 * (120 - 110))
