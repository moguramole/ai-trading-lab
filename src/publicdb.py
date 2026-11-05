"""公開用のDBを作る。個人の持ち株を取り除き、クラウド（GitHub）に置いてよい形にする。

    python -m src.publicdb data/lab.db data/public.db

取り除くもの: holdings（持ち株）の全行、TOPIX500・市場指標以外の銘柄の株価（持ち株のためだけに取得した分）。
元のDBは書き換えない。
"""
import sqlite3
import sys
from pathlib import Path

from . import fetch


def make(src, dst) -> dict:
    """srcを読み取り専用で開いてdstへ複製し、持ち株と関連する株価を消す。取り除いた件数を返す。"""
    src, dst = Path(src), Path(dst)
    if dst.exists():
        dst.unlink()
    s = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
    d = sqlite3.connect(dst)
    try:
        s.backup(d)
    finally:
        s.close()
    try:
        tables = {r[0] for r in d.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        n_hold = 0
        if "holdings" in tables:
            n_hold = d.execute("DELETE FROM holdings").rowcount
        n_px = 0
        if {"prices", "universe"} <= tables:
            keep = [*fetch.BENCHMARKS]
            marks = ",".join("?" * len(keep))
            n_px = d.execute(
                f"DELETE FROM prices WHERE ticker NOT IN (SELECT ticker FROM universe) AND ticker NOT IN ({marks})",
                keep).rowcount
        d.commit()
        d.execute("VACUUM")
    finally:
        d.close()
    return {"holdings_removed": n_hold, "prices_removed": n_px}


def main(argv=None) -> int:
    a = list(argv if argv is not None else sys.argv[1:])
    if len(a) != 2:
        print("使い方: python -m src.publicdb <元DB> <出力DB>")
        return 2
    r = make(*a)
    print(f"持ち株{r['holdings_removed']}行・持ち株用の株価{r['prices_removed']}行を除いて {a[1]} を作成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
