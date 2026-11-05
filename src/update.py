"""日次の株価更新。 python -m src.update [--period 1mo]

対象はJPX公式一覧のTOPIX500 + 市場指標。銘柄ごとに検証して保存し、検証に落ちた銘柄は
保存せず他は続行する。失敗が全体の5%を超えたら終了コード1（数銘柄の欠損は通常あり得る）。
再実行しても (ticker, date) 主キーのUPSERTなので重複しない。
"""
import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from . import alert, data, fetch, news, portfolio, runner, schedule, status, store, universe

ROOT = Path(__file__).resolve().parent.parent
WATCHLIST = ROOT / "watchlist.txt"  # 一覧が取れず保存分もないときの予備
LOG = ROOT / "data" / "update.log"
MAX_FAIL_RATIO = 0.05


def target_tickers(con, watchlist=WATCHLIST) -> list[str]:
    uni, msg = universe.refresh(con)
    logging.info("銘柄一覧: %s", msg)
    base = list(uni["ticker"]) if len(uni) else fetch.read_watchlist(watchlist)
    if not len(uni):
        logging.warning("銘柄一覧がないため watchlist.txt を使用")
    held = list(portfolio.load(con)["ticker"])   # 個人の持ち株は、TOPIX500外でも価格を取得する
    return base + [t for t in [*held, *fetch.BENCHMARKS] if t not in base]


def run(period="1mo", db_path=data.DB_PATH, fetcher=fetch.fetch_many, tickers=None, notifier=None, now=None, news_fn=None) -> int:
    """notifierは通知関数(title, message)。失敗時はアラートファイルも書く（成功すれば消す）。"""
    alert_path = Path(db_path).parent / "alert.json"
    try:
        code, problems = _run(period, db_path, fetcher, tickers, now, news_fn)
    except Exception as e:  # 取得全体が落ちた場合も、黙って終わらせず通知する
        logging.error("更新が異常終了: %s", e)
        code, problems = 1, [f"更新が異常終了: {e}"]
    if problems:
        alert.write(alert_path, "株価の取得に問題があります。判断・約定の前に確認してください", problems)
        if notifier:
            notifier("AI Trading Lab: 株価の取得に問題", problems[0])
    else:
        alert.clear(alert_path)
    return code


def _run(period, db_path, fetcher, tickers, now, news_fn=None) -> tuple[int, list[str]]:
    con = data.connect(db_path)
    tickers = tickers if tickers is not None else target_tickers(con)
    results = fetcher(tickers, period)
    failed, saved = [], 0
    for t in tickers:
        try:
            r = results[t]
            if isinstance(r, Exception):
                raise r
            data.save_prices(con, data.validate(r), fetch.SOURCE)
            saved += 1
        except Exception as e:  # 異常データは保存せず、他銘柄は続行
            failed.append(t)
            logging.error("%s: 失敗 %s", t, e)
    logging.info("保存 %d銘柄 / 失敗 %d銘柄 / 対象 %d銘柄", saved, len(failed), len(tickers))
    problems = []
    if len(failed) > len(tickers) * MAX_FAIL_RATIO:
        logging.error("失敗が多すぎます: %s", failed[:20])
        problems.append(f"{len(tickers)}銘柄中{len(failed)}銘柄の取得に失敗（例: {', '.join(failed[:5])}）")
    lag = stale_lag(con, now)
    if lag:
        logging.error("株価が最新になっていません（%d営業日遅れ）", lag)
        problems.append(f"保存されている株価が最新の営業日から{lag}営業日遅れています")
    if news_fn:
        try:  # 開示の取得失敗は株価の更新を止めない（ログに残すだけ。依頼文は「該当なし」になる）
            logging.info("適時開示: 新規%d件", news_fn(con, datetime.now(fetch.JST).date()))
        except Exception as e:
            logging.warning("適時開示の取得に失敗: %s", e)
    if record_ledger(con) != 0:
        problems.append("取引履歴の記録に失敗、または記録済みの約定と再計算が不一致（更新ログを確認）")
    con.close()
    return (1 if problems else 0), problems


def stale_lag(con, now=None) -> int:
    """取得に成功したように見えても、日付が進んでいないケース（取得元の停止など）を検出する。"""
    latest = con.execute("SELECT MAX(date) FROM prices").fetchone()[0]
    if latest is None:
        return 0
    return status.business_lag(pd.Timestamp(latest), schedule.expected_latest(now))


def record_ledger(con) -> int:
    """最新データで仮想売買を再計算し、約定・拒否・日次資産をSQLiteに追記する。"""
    try:
        prices, flags = data.read_clean(con)
        if prices.empty:
            return 0
        for f in flags.itertuples():
            logging.warning("価格補正 %s %s %s: %s", f.ticker, f.date, f.kind, f.detail)
        stocks, _ = runner.split_benchmarks(prices, portfolio.extra_tickers(con, universe.load(con)))
        accounts, _ = runner.build_accounts(con, stocks)
        n = store.record_ledger(con, accounts)["new_rows"]
        bad = store.ledger_mismatches(con, accounts)
        logging.info("取引履歴を記録: 新規%d行", n)
        if bad:
            logging.error("記録済みの約定と再計算が不一致: %s", bad[:10])
            return 1
        return 0
    except Exception as e:  # 履歴の記録失敗は株価保存とは別に報告する
        logging.error("取引履歴の記録に失敗: %s", e)
        return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", default="1mo", help="取得期間。初回は 1y など長めに")
    args = ap.parse_args(argv)
    LOG.parent.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(LOG, encoding="utf-8"), logging.StreamHandler()])
    return run(args.period, notifier=alert.notify, news_fn=news.update)


if __name__ == "__main__":
    sys.exit(main())


