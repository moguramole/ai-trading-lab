import pandas as pd

from src import data, holdings_cli, news, portfolio


def test_digest_for_holdings(tmp_path):
    con = data.connect(tmp_path / "t.db")
    px = data.load_csv("sample/sample_prices.csv")
    data.save_prices(con, px, "test")
    t = px["ticker"].iloc[0]
    portfolio.upsert(con, t, 10, 1000)
    portfolio.upsert(con, "9999", 5, 2000)                                  # 価格が取れていない銘柄
    as_of, text = holdings_cli.build_digest(con)
    assert as_of == px["date"].max().strftime("%Y-%m-%d")
    assert f"## {t}" in text and "## 9999" in text and "価格を取得できていない" in text
    assert "平均取得単価 1,000円" in text and "評価損益" in text


def test_digest_says_disclosure_coverage(tmp_path):
    con = data.connect(tmp_path / "t.db")
    px = data.load_csv("sample/sample_prices.csv")
    data.save_prices(con, px, "test")
    t = px["ticker"].iloc[0]
    portfolio.upsert(con, t, 10, 1000)
    day = px["date"].max().strftime("%Y-%m-%d")
    news.save(con, pd.DataFrame([("u1", day, "10:00", t, "n", "自己株式の取得")], columns=news.COLUMNS))
    _, text = holdings_cli.build_digest(con, now=px["date"].max().to_pydatetime())
    assert "自己株式の取得" in text


def test_digest_without_holdings(tmp_path):
    con = data.connect(tmp_path / "t.db")
    data.save_prices(con, data.load_csv("sample/sample_prices.csv"), "test")
    assert "登録されていません" in holdings_cli.build_digest(con)[1]
