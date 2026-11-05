import pandas as pd
import pytest

from src import data, portfolio


@pytest.fixture
def con(tmp_path):
    return data.connect(tmp_path / "t.db")


SNAP = pd.DataFrame([
    {"ticker": "8035", "name": "東京エレクトロン", "sector33": "電気機器", "close": 9805.0, "ret1": 0.044, "ret20": -0.148},
    {"ticker": "8058", "name": "三菱商事", "sector33": "卸売業", "close": 4847.0, "ret1": 0.0, "ret20": 0.01},
])


def test_upsert_overwrites_same_ticker(con):
    portfolio.upsert(con, "8035", 15, 12337)
    portfolio.upsert(con, "8035", 20, 12000)
    h = portfolio.load(con)
    assert len(h) == 1 and h.iloc[0]["qty"] == 20 and h.iloc[0]["avg_cost"] == 12000


def test_remove(con):
    portfolio.upsert(con, "8035", 15, 12337)
    portfolio.remove(con, "8035")
    assert portfolio.load(con).empty


@pytest.mark.parametrize("args", [("", 1, 100), ("8035", 0, 100), ("8035", -1, 100),
                                  ("8035", 1.5, 100), ("8035", 1, 0), ("8035", 1, -5), ("8035", True, 100)])
def test_invalid_holdings_rejected(con, args):
    with pytest.raises(portfolio.InvalidHolding):
        portfolio.upsert(con, *args)
    assert portfolio.load(con).empty


def test_valuation_and_totals():
    h = pd.DataFrame([("8035", 15, 12337.0), ("8058", 20, 4584.0)], columns=["ticker", "qty", "avg_cost", ])
    v = portfolio.valuation(h, SNAP).set_index("ticker")
    assert v.loc["8035", "cost"] == pytest.approx(15 * 12337)
    assert v.loc["8035", "value"] == pytest.approx(15 * 9805)
    assert v.loc["8035", "pnl"] == pytest.approx(15 * (9805 - 12337))   # 含み損
    assert v.loc["8058", "pnl_pct"] == pytest.approx(4847 / 4584 - 1)   # 含み益
    assert v["weight"].sum() == pytest.approx(1.0)
    t = portfolio.totals(v.reset_index())
    assert t["pnl"] == pytest.approx(v["pnl"].sum()) and t["unpriced"] == 0


def test_unpriced_ticker_excluded_from_totals():
    h = pd.DataFrame([("8035", 15, 12337.0), ("9999", 10, 1000.0)], columns=["ticker", "qty", "avg_cost"])
    v = portfolio.valuation(h, SNAP)
    t = portfolio.totals(v)
    assert t["unpriced"] == 1 and t["cost"] == pytest.approx(15 * 12337)   # 価格の無い銘柄は合計に入れない
    assert v[v.ticker == "9999"]["close"].isna().all()
