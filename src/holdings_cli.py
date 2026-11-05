"""持ち株の評価エージェント向けのコマンド。評価に使うデータを1つの文書にまとめて書き出す。

    python -m src.holdings_cli digest

個人の持ち株の評価専用。仮想売買（signals・口座・依頼文）には一切つながない。
出力は data/reviews/digest_YYYY-MM-DD.md。評価の文書は data/reviews/review_YYYY-MM-DD.md に保存する。
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from . import data, fetch, market, news, portfolio, runner, universe

OUT = Path(__file__).resolve().parent.parent / "data" / "reviews"
NEWS_DAYS = 14


def _pct(x) -> str:
    return "—" if pd.isna(x) else f"{x:+.1%}"


def _history(prices: pd.DataFrame, ticker: str) -> dict:
    g = prices[prices["ticker"] == ticker].sort_values("date")
    if g.empty:
        return {}
    c = g["close"].reset_index(drop=True)
    daily = c.pct_change().dropna()
    hi, lo = g["high"].max(), g["low"].min()
    return {"days": len(g), "from": g["date"].iloc[0].strftime("%Y-%m-%d"), "high": hi, "low": lo,
            "from_high": c.iloc[-1] / hi - 1, "from_low": c.iloc[-1] / lo - 1,
            "ret60": c.iloc[-1] / c.iloc[-61] - 1 if len(c) > 60 else float("nan"),
            "vol20": float(daily.tail(20).std()) if len(daily) >= 20 else float("nan"),
            "worst_day20": float(daily.tail(20).min()) if len(daily) >= 20 else float("nan")}


def build_digest(con, now: datetime | None = None) -> tuple[str, str]:
    """(判断に使う日付, 文書の本文)。"""
    now = now or datetime.now(fetch.JST)
    prices, _ = data.read_clean(con)
    if prices.empty:
        raise RuntimeError("株価データがありません")
    uni = universe.load(con)
    all_stocks, _ = runner.split_benchmarks(prices)
    stocks, bench = runner.split_benchmarks(prices, portfolio.extra_tickers(con, uni))
    snap_all, snap = market.snapshot(all_stocks, uni), market.snapshot(stocks, uni)
    as_of = prices["date"].max().strftime("%Y-%m-%d")
    hold = portfolio.load(con)
    if hold.empty:
        return as_of, "持ち株は登録されていません。"
    v = portfolio.valuation(hold, snap_all)
    t = portfolio.totals(v)
    since = (now - timedelta(days=NEWS_DAYS)).strftime("%Y-%m-%d")
    disc = news.load(con, since)
    first_disc = disc["date"].min() if len(disc) else None  # 取得できている開示の最も古い日（それ以前は「なし」ではなく「不明」）

    L = [f"# 持ち株の評価用データ（株価は {as_of} の終値まで）", "",
         "個人の持ち株の評価専用。仮想売買の口座・判断とは無関係。評価は終値ベースで、配当・手数料・税金は含まない。", "",
         "## 全体", f"- 評価額 {t['value']:,.0f}円 / 取得原価 {t['cost']:,.0f}円 / 評価損益 {t['pnl']:+,.0f}円（{_pct(t['pnl_pct'])}）",
         f"- 保有 {t['n']}銘柄" + (f"（うち価格なし {t['unpriced']}銘柄）" if t["unpriced"] else ""), ""]
    sec = v.dropna(subset=["close"]).groupby("sector33")["value"].sum().sort_values(ascending=False)
    if len(sec):
        L.append("業種の構成比: " + " / ".join(f"{k or '不明'} {x / sec.sum():.0%}" for k, x in sec.items()))
        L.append("")

    b = market.breadth(snap)
    L += ["## 市場の状況"]
    for r in market.index_table(bench, fetch.BENCHMARKS).itertuples():
        L.append(f"- {r.指標}: {r.値:,.1f}（本日{_pct(r.本日)} / 5日{_pct(r.五日)} / 20日{_pct(r.二十日)}）")
    if b:
        L.append(f"- 対象{b['銘柄数']}銘柄: 20日線の上 {b['20日線の上']:.0%} / 60日線の上 {b['60日線の上']:.0%} / "
                 f"20日平均騰落 {_pct(b['20日平均騰落'])}")
    secs = market.sector_table(snap)
    L.append("")
    for r in v.itertuples():
        L += [f"## {r.ticker} {r.name}（{r.sector33 or '業種不明'}）"]
        if pd.isna(r.close):
            L += ["- 価格を取得できていない銘柄（TOPIX500外で、まだ価格が取得されていない可能性）",
                  f"- 保有 {r.qty:,}株 / 平均取得単価 {r.avg_cost:,.0f}円", ""]
            continue
        h = _history(prices, r.ticker)
        row = snap_all[snap_all["ticker"] == r.ticker].iloc[0]
        L += [f"- 保有 {r.qty:,}株 / 平均取得単価 {r.avg_cost:,.0f}円 / 終値 {r.close:,.1f}円",
              f"- 評価額 {r.value:,.0f}円（構成比 {r.weight:.0%}）/ 評価損益 {r.pnl:+,.0f}円（{_pct(r.pnl_pct)}）",
              f"- 騰落: 本日{_pct(row.ret1)} / 5日{_pct(row.ret5)} / 20日{_pct(row.ret20)} / 60日{_pct(h['ret60'])}",
              f"- 移動平均との差: 20日線{_pct(row.gap20)} / 60日線{_pct(row.gap60)}（20日線が60日線の{'上' if row.trend_up else '下'}）",
              f"- 取得済み期間（{h['from']}〜、{h['days']}日分）の高値 {h['high']:,.0f} / 安値 {h['low']:,.0f}。"
              f"高値から{_pct(h['from_high'])} / 安値から{_pct(h['from_low'])}",
              f"- 値動きの大きさ: 直近20日の日次変動（標準偏差）{h['vol20']:.1%} / 直近20日で最悪の1日 {_pct(h['worst_day20'])}",
              f"- 出来高: 直近20日平均の{row.vol_ratio:.1f}倍（本日）"]
        if r.sector33 and len(secs) and r.sector33 in set(secs["業種"]):
            s = secs[secs["業種"] == r.sector33].iloc[0]
            rank = int(secs.index[secs["業種"] == r.sector33][0]) + 1
            L.append(f"- 同業種（{s.銘柄数}銘柄）の20日平均騰落 {_pct(s.二十日)}（33業種中{rank}位 / 全体平均 {_pct(b.get('20日平均騰落', float('nan')))}）")
        d = disc[disc["ticker"] == r.ticker]
        cover = f"（取得済みの期間は {first_disc[5:].replace(chr(45), chr(47))} 以降のみ）" if first_disc and first_disc > since else ""
        L.append(f"- 適時開示（題名のみ）{cover}: " + ("該当なし" if d.empty else ""))
        L += [f"  - {x.date[5:].replace('-', '/')} {x.time} {x.title}" for x in d.itertuples()]
        L.append("")
    return as_of, "\n".join(L)


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if (argv or sys.argv[1:2]) != ["digest"]:
        print("使い方: python -m src.holdings_cli digest")
        return 2
    con = data.connect()
    try:
        as_of, text = build_digest(con)
    finally:
        con.close()
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"digest_{as_of}.md"
    path.write_text(text, encoding="utf-8")
    print(f"株価日: {as_of}\n評価用データ: {path}\n評価の保存先: {OUT / f'review_{as_of}.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

