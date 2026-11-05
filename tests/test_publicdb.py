import sqlite3

from src import publicdb


def _src(path):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE holdings (ticker TEXT, qty INTEGER, avg_cost REAL, updated_at TEXT)")
    c.execute("INSERT INTO holdings VALUES ('9999', 100, 500, 'x')")
    c.execute("CREATE TABLE universe (ticker TEXT, name TEXT)")
    c.execute("INSERT INTO universe VALUES ('7203', 'トヨタ')")
    c.execute("CREATE TABLE prices (ticker TEXT, date TEXT, close REAL)")
    c.executemany("INSERT INTO prices VALUES (?,?,?)",
                  [("7203", "2026-11-05", 1), ("9999", "2026-11-05", 2), ("^N225", "2026-11-05", 3)])
    c.commit()
    c.close()


def test_make_removes_holdings_and_their_prices(tmp_path):
    src, dst = tmp_path / "a.db", tmp_path / "b.db"
    _src(src)
    r = publicdb.make(src, dst)
    assert r == {"holdings_removed": 1, "prices_removed": 1}
    d = sqlite3.connect(dst)
    assert d.execute("SELECT COUNT(*) FROM holdings").fetchone()[0] == 0
    assert {t for (t,) in d.execute("SELECT ticker FROM prices")} == {"7203", "^N225"}
    d.close()
    s = sqlite3.connect(src)  # 元のDBは変わらない
    assert s.execute("SELECT COUNT(*) FROM holdings").fetchone()[0] == 1
    s.close()
