"""スマホ向けの静的ページを作る。 python -m src.site <DB> <出力フォルダ>

載せるのは仮想売買の成績・判断・取引と、市場の概況だけ。個人の持ち株は一切読まない・載せない。
外部ファイルに依存しない1枚のHTML（グラフもSVG）。日本の慣習に合わせ、上昇は朱・下落は藍。
"""
import html
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from . import data, fetch, market, metrics, runner, store, universe
from .ai_adapters import JST

LABEL = {"gpt": "GPT", "claude": "Claude", "baseline": "AIなし"}
COLOR = {"gpt": "#2a7f62", "claude": "#c4621b", "baseline": "#6b7280", "^N225": "#9ca3af", "1306": "#bfa24a"}
UP, DOWN = "#c8301f", "#1f4e9c"  # 朱 / 藍


def esc(x) -> str:
    return html.escape(str(x))


def pct(x, d=1) -> str:
    return "–" if pd.isna(x) else f"{x * 100:+.{d}f}%"


def sign_class(x) -> str:
    return "" if pd.isna(x) or x == 0 else ("up" if x > 0 else "down")


def yen(x) -> str:
    return f"{x:,.0f}円"


def chart(series: dict[str, pd.DataFrame], width=640, height=260) -> str:
    """{名前: DataFrame(date, value)} を折れ線のSVGにする。線の右端に名前と値を直接書く。"""
    series = {k: v for k, v in series.items() if len(v)}
    if not series:
        return "<p class='muted'>データがありません</p>"
    allv = pd.concat(series.values())
    lo, hi = allv["value"].min(), allv["value"].max()
    pad = (hi - lo) * 0.08 or 1
    lo, hi = lo - pad, hi + pad
    d0, d1 = allv["date"].min(), allv["date"].max()
    span = max((d1 - d0).days, 1)
    L, R, T, B = 8, 118, 8, 22
    x = lambda d: L + (width - L - R) * (d - d0).days / span
    y = lambda v: T + (height - T - B) * (1 - (v - lo) / (hi - lo))
    out = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="資産推移">']
    for i in range(5):  # 横の目盛り線
        v = lo + (hi - lo) * i / 4
        out.append(f'<line x1="{L}" x2="{width - R}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="grid"/>'
                   f'<text x="{width - R + 4}" y="{y(v) + 3:.1f}" class="tick">{v / 1000:,.0f}k</text>')
    out.append(f'<text x="{L}" y="{height - 6}" class="tick">{d0:%Y-%m-%d}</text>'
               f'<text x="{width - R}" y="{height - 6}" class="tick" text-anchor="end">{d1:%Y-%m-%d}</text>')
    ends = []
    for k, df in series.items():
        pts = " ".join(f"{x(d):.1f},{y(v):.1f}" for d, v in zip(df["date"], df["value"]))
        main = k in LABEL
        dash = "" if main else 'stroke-dasharray="4 3"'
        out.append(f'<polyline points="{pts}" fill="none" stroke="{COLOR.get(k, "#888")}" '
                   f'stroke-width="{2.2 if main else 1.2}" {dash}/>')
        ends.append([k, y(df["value"].iloc[-1]), df["value"].iloc[-1]])
    ends.sort(key=lambda e: e[1])
    for i in range(1, len(ends)):  # 右端のラベルが重ならないようにずらす
        ends[i][1] = max(ends[i][1], ends[i - 1][1] + 12)
    names = {**LABEL, **{k: v for k, v in fetch.BENCHMARKS.items()}}
    for k, yy, v in ends:
        out.append(f'<text x="{width - R + 28}" y="{yy + 3:.1f}" class="end" fill="{COLOR.get(k, "#888")}">'
                   f'{esc(names.get(k, k))} {v / 1000:,.1f}k</text>')
    out.append("</svg>")
    return "".join(out)


def table(headers, rows, num=()) -> str:
    if not rows:
        return "<p class='muted'>なし</p>"
    th = "".join(f"<th{' class=n' if i in num else ''}>{esc(h)}</th>" for i, h in enumerate(headers))
    body = "".join("<tr>" + "".join(
        f"<td{' class=n' if i in num else ''}>{c}</td>" for i, c in enumerate(r)) + "</tr>" for r in rows)
    return f'<div class="scroll"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def colored(x, d=1) -> str:
    return f'<span class="{sign_class(x)}">{pct(x, d)}</span>'


def build_html(con, now=None) -> str:
    now = now or datetime.now(JST)
    prices, _ = data.read_clean(con)
    uni = universe.load(con)
    stocks, bench = runner.split_benchmarks(prices)
    snap = market.snapshot(stocks, uni)
    accounts, _ = runner.build_accounts(con, stocks)
    names = dict(zip(uni["ticker"], uni["name"])) if len(uni) else {}
    nm = lambda t: esc(names.get(t, t))
    as_of = prices["date"].max()
    last = {t: float(g["close"].iloc[-1]) for t, g in stocks.groupby("ticker")}

    # --- 資産推移 ---
    series = {m: accounts[m][1].rename(columns={"equity": "value"})[["date", "value"]] for m in runner.MODELS}
    start = min(df["date"].min() for df in series.values() if len(df))
    for t in fetch.BENCHMARKS:
        b = bench[(bench["ticker"] == t) & (bench["date"] >= start)].sort_values("date")
        if len(b):
            series[t] = pd.DataFrame({"date": b["date"], "value": runner.INITIAL * b["close"] / b["close"].iloc[0]})
    perf = []
    for m in runner.MODELS:
        broker, curve = accounts[m]
        eq = float(curve["equity"].iloc[-1]) if len(curve) else runner.INITIAL
        wr, n = metrics.win_rate(broker.trades)
        perf.append([LABEL[m], yen(eq), colored(eq / runner.INITIAL - 1, 2),
                     colored(metrics.max_drawdown(curve["equity"]) if len(curve) else 0, 1),
                     f"{wr * 100:.0f}%（{n}回）" if wr is not None else "–", str(len(broker.trades))])

    # --- 口座（GPT / Claude）---
    acct = []
    for m in ("gpt", "claude"):
        broker, _ = accounts[m]
        pos = [[f"{esc(t)} {nm(t)}", f"{q:,}", yen(last.get(t, 0)), yen(q * last.get(t, 0))]
               for t, q in sorted(broker.positions.items())]
        sigs = store.load_signals(con, m)
        latest = max((s.as_of for s in sigs), default=None)
        today = [[esc(s.ticker) + " " + nm(s.ticker), {"buy": "買い", "sell": "売り", "hold": "見送り"}[s.action],
                  f"{s.qty:,}" if s.qty else "–", esc(s.reason)] for s in sigs if s.as_of == latest]
        trades = sorted(broker.trades, key=lambda t: t.trade_date, reverse=True)[:15]
        tr = [[esc(t.trade_date), esc(t.ticker) + " " + nm(t.ticker), "買" if t.side == "buy" else "売",
               f"{t.qty:,}", f"{t.price:,.1f}"] for t in trades]
        acct.append(f"<section><h2>{LABEL[m]}の口座</h2>"
                    f"<p class='muted'>現金 {yen(broker.cash)}</p>"
                    f"<h3>保有</h3>{table(['銘柄', '株数', '終値', '評価額'], pos, num=(1, 2, 3))}"
                    f"<h3>直近の判断（判断日 {esc(latest or '–')}）</h3>{table(['銘柄', '判断', '株数', '理由'], today, num=(2,))}"
                    f"<h3>直近の約定</h3>{table(['約定日', '銘柄', '売買', '株数', '価格'], tr, num=(3, 4))}</section>")

    # --- 市場概況 ---
    idx = market.index_table(bench, fetch.BENCHMARKS)
    idx_rows = [[esc(r["指標"]), f"{r['値']:,.1f}", colored(r["本日"]), colored(r["五日"]), colored(r["二十日"])]
                for _, r in idx.iterrows()]
    br = market.breadth(snap)
    weak = br and br["60日線の上"] < market.MKT_MIN
    br_txt = (f"対象{br['銘柄数']}銘柄: 本日上昇 {br['本日上昇'] * 100:.0f}% / 20日線の上 {br['20日線の上'] * 100:.0f}% / "
              f"60日線の上 {br['60日線の上'] * 100:.0f}% / 20日高値更新 {br['20日高値更新']}銘柄") if br else ""
    sec = market.sector_table(snap)
    sec_rows = [[esc(r["業種"]), colored(r["本日"]), colored(r["五日"]), colored(r["二十日"])] for _, r in sec.iterrows()]
    top = snap.sort_values("ret1", ascending=False)
    mv_rows = lambda df: [[f"{esc(r['ticker'])} {esc(r['name'] if pd.notna(r['name']) else r['ticker'])}",
                           f"{r['close']:,.1f}", colored(r["ret1"])] for _, r in df.iterrows()]

    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>AIトレード研究所</title>
<style>
:root{{--bg:#fbfaf7;--fg:#1d1d1b;--mut:#6b6b66;--line:#dcd9d0;--up:{UP};--down:{DOWN}}}
@media(prefers-color-scheme:dark){{:root{{--bg:#161614;--fg:#ecebe6;--mut:#9a9890;--line:#3a3935;--up:#ff6b57;--down:#6fa0f0}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 "BIZ UDPGothic","Hiragino Sans","Yu Gothic",sans-serif}}
main{{max-width:760px;margin:0 auto;padding:16px}}
h1{{font-size:1.25rem;margin:.2rem 0}} h2{{font-size:1.05rem;margin:2rem 0 .4rem;padding-top:.8rem;border-top:1px solid var(--line)}}
h3{{font-size:.9rem;margin:1rem 0 .2rem;color:var(--mut)}}
.muted{{color:var(--mut);font-size:.85rem;margin:.2rem 0}}
.warn{{border-left:4px solid var(--up);padding:.2rem .7rem;margin:.8rem 0}}
.scroll{{overflow-x:auto}} table{{border-collapse:collapse;width:100%;font-size:.85rem}}
th,td{{padding:.35rem .5rem;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{color:var(--mut);font-weight:normal;white-space:nowrap}} .n{{text-align:right;white-space:nowrap}}
.up{{color:var(--up)}} .down{{color:var(--down)}}
svg{{width:100%;height:auto}} .grid{{stroke:var(--line);stroke-width:1}}
.tick{{font-size:10px;fill:var(--mut)}} .end{{font-size:11px;font-weight:bold}}
</style></head><body><main>
<h1>AIトレード研究所</h1>
<p class="muted">仮想売買のみ（実際の注文はありません）。データ日: {as_of:%Y-%m-%d} ／ 作成: {now:%Y-%m-%d %H:%M} JST。
株価は非公式の取得元で、正確性は保証されません。投資助言ではありません。</p>
{'<p class="warn">地合いが弱い日です（60日線の上の銘柄が50%未満）。</p>' if weak else ''}
<h2>資産推移（初期資金 30万円）</h2>
{chart(series)}
{table(['口座', '総資産', '損益', '最大DD', '勝率', '約定数'], perf, num=(1, 2, 3, 5))}
<p class="muted">取引回数が少ないうちは偶然の影響が大きく、優劣の根拠にはなりません。</p>
{''.join(acct)}
<h2>市場概況</h2>
{table(['指標', '値', '本日', '5日', '20日'], idx_rows, num=(1, 2, 3, 4))}
<p class="muted">{esc(br_txt)}</p>
<h3>本日の上昇上位</h3>{table(['銘柄', '終値', '本日'], mv_rows(top.head(8)), num=(1, 2))}
<h3>本日の下落上位</h3>{table(['銘柄', '終値', '本日'], mv_rows(top.tail(8).iloc[::-1]), num=(1, 2))}
<h3>業種別（20日騰落の降順）</h3>{table(['業種', '本日', '5日', '20日'], sec_rows, num=(1, 2, 3))}
</main></body></html>
"""


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    a = list(argv if argv is not None else sys.argv[1:])
    if len(a) != 2:
        print("使い方: python -m src.site <DB> <出力フォルダ>")
        return 2
    con = data.connect(a[0])
    try:
        out = Path(a[1])
        out.mkdir(parents=True, exist_ok=True)
        (out / "index.html").write_text(build_html(con), encoding="utf-8")
        (out / ".nojekyll").write_text("")
    finally:
        con.close()
    print(f"作成: {out / 'index.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
