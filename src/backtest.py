"""AIなしのルール戦略を過去データで検証する。 python -m src.backtest

ルールは事前に固定（パラメータ調整はしない）。判断はその日の終値までの指標だけで行い、
約定は翌営業日の始値（本番の仮想売買と同じ PaperBroker・同じリスク制限・同じ手数料）。
注意: 銘柄は「現在の」TOPIX500なので、過去に除外された銘柄が含まれない生存者バイアスがある。
"""
import numpy as np
import pandas as pd

from . import fetch, metrics, runner, sim
from .ai_adapters import Signal
from .market import MAX_PRICE, MKT_MIN
from .paper_broker import PaperBroker

WARMUP = 60        # SMA60などの助走期間（この間は判断しない）
HOLD_DAYS = 10     # ルール戦略の保有日数（日数経過で売り）
TOP_N = 2          # 1日に買う銘柄数
YEN = 25_000       # 1回の買付額
STRATEGIES = ("sma_cross", "pullback", "momentum")
RANDOM_RUNS = 10   # ランダム選択（対照）を何回走らせて平均するか


def indicators(stocks: pd.DataFrame) -> dict:
    """日付×銘柄の表で指標を一括計算する。"""
    p = lambda col: stocks.pivot(index="date", columns="ticker", values=col).sort_index()
    close, high, vol = p("close"), p("high"), p("volume")
    sma20, sma60 = close.rolling(20).mean(), close.rolling(60).mean()
    return {
        "close": close, "sma20": sma20, "sma60": sma60,
        "ret5": close.pct_change(5), "ret20": close.pct_change(20),
        "turnover20": (close * vol).rolling(20).mean(),
        "vol_ratio": vol / vol.rolling(20).mean(),
        "new_high20": close >= high.shift(1).rolling(20).max(),
        # 地合い: 60日線の上にある銘柄の割合（その日の終値までで計算）
        "breadth60": (close > sma60).where(sma60.notna()).mean(axis=1),
    }


# 地合いフィルターの閾値は market.MKT_MIN（60日線の上にある銘柄がこの割合未満の日は買わない。事前に固定した値）
# 候補ルールの変更案（結果を見る前に決めた全リスト。これ以外は試していない）
#   _mkt  = 地合いフィルター付き / _deep = 5日の下げが深い順に選ぶ（元は20日騰落の大きい順）
VARIANTS = ("pullback", "pullback_mkt", "pullback_deep", "pullback_mkt_deep", "momentum", "momentum_mkt")


def rule_signals(ind: dict, kind: str, seed: int = 0) -> list[Signal]:
    """pullback / momentum / random とその変更案の売買シグナル。保有日数が過ぎたら同数量を売る。"""
    close, dates = ind["close"], ind["close"].index
    base, *opts = kind.split("_")
    if base not in ("pullback", "momentum", "random") or set(opts) - {"mkt", "deep"}:
        raise ValueError(kind)
    rng = np.random.default_rng(seed)
    out = []
    for i in range(WARMUP, len(dates)):
        if "mkt" in opts and not ind["breadth60"].iloc[i] >= MKT_MIN:
            continue   # 地合いが弱い日は新規に買わない
        row = lambda k: ind[k].iloc[i]
        c = row("close")
        liquid = (row("turnover20") >= row("turnover20").median()) & (c <= MAX_PRICE)
        if base == "pullback":
            m = liquid & (row("sma20") > row("sma60")) & (c > row("sma60")) & (row("ret5") < 0)
            rank = row("ret5")[m].sort_values() if "deep" in opts else row("ret20")[m].sort_values(ascending=False)
            pick = rank.head(TOP_N).index
        elif base == "momentum":
            m = liquid & row("new_high20") & (row("vol_ratio") >= 1.3)
            pick = row("vol_ratio")[m].sort_values(ascending=False).head(TOP_N).index
        else:
            cand = list(c[liquid.fillna(False)].index)
            pick = rng.choice(cand, size=min(TOP_N, len(cand)), replace=False) if cand else []
        for t in pick:
            qty = max(1, int(YEN // c[t]))
            out.append(Signal(kind, dates[i].strftime("%Y-%m-%d"), t, "buy", 0.5, kind, "検証用", qty=qty))
            if i + HOLD_DAYS < len(dates):
                out.append(Signal(kind, dates[i + HOLD_DAYS].strftime("%Y-%m-%d"), t, "sell", 0.5,
                                  f"{HOLD_DAYS}日保有後", "検証用", qty=qty))
    return out


def benchmark_curves(ind: dict, bench: pd.DataFrame, dates, initial=runner.INITIAL) -> dict:
    """評価期間の初日を初期資金に揃えた指数・全銘柄等金額保有の資産推移。"""
    out = {}
    for t, label in fetch.BENCHMARKS.items():
        c = bench[bench["ticker"] == t].set_index("date")["close"].reindex(dates).ffill()  # 除去した異常日は前日値
        if c.notna().all():
            out[label] = initial * c / c.iloc[0]
    c = ind["close"].reindex(dates)
    ok = c.columns[c.notna().all()]
    out["全銘柄を等金額で保有"] = initial * (c[ok] / c[ok].iloc[0]).mean(axis=1)
    return out


def stats(curve: pd.Series, trades=()) -> dict:
    wr, n = metrics.win_rate(list(trades))
    return {"総リターン": curve.iloc[-1] / curve.iloc[0] - 1, "最大DD": metrics.max_drawdown(curve),
            "約定数": len(trades), "勝率": wr, "売却数": n}


def _evaluator(stocks, ind):
    """(評価日, 前半/後半の境目, execute, row)。run_all と run_variants で共有する。"""
    dates = ind["close"].index[WARMUP:]
    mid = dates[len(dates) // 2]

    def execute(name, sigs):
        b = PaperBroker(name)
        curve = sim.run(b, stocks, sigs).set_index("date")["equity"].reindex(dates)
        return curve, b

    def row(curve, trades=()):
        first, second = curve[curve.index < mid], curve[curve.index >= mid]
        s = stats(curve, trades)
        s.update({"前半(調整期)": first.iloc[-1] / first.iloc[0] - 1,
                  "後半(未知)": second.iloc[-1] / second.iloc[0] - 1})
        return s

    return dates, mid, execute, row


def run_variants(stocks: pd.DataFrame, bench: pd.DataFrame, kinds=VARIANTS) -> dict:
    """候補ルールの変更案を、同じ条件で並べて比べる。ランダム平均と全銘柄保有を基準線として付ける。"""
    ind = indicators(stocks)
    dates, mid, execute, row = _evaluator(stocks, ind)
    rows, curves = {}, {}
    for k in kinds:
        curves[k], b = execute(k, rule_signals(ind, k))
        rows[k] = row(curves[k], b.trades)
    runs = [execute("random", rule_signals(ind, "random", seed=s)) for s in range(RANDOM_RUNS)]
    rows["random（10回平均）"] = pd.DataFrame([row(c, b.trades) for c, b in runs]).mean(numeric_only=True).to_dict()
    for label, c in benchmark_curves(ind, bench, dates).items():
        if label == "全銘柄を等金額で保有":
            rows[label] = row(c)
    return {"table": pd.DataFrame(rows).T.rename_axis("ルール"), "curves": curves, "split": mid}


def run_all(stocks: pd.DataFrame, bench: pd.DataFrame) -> dict:
    """{'curves': {名前: 資産推移Series(評価期間)}, 'table': 成績表, 'split': 前半/後半の境目}"""
    ind = indicators(stocks)
    dates, mid, execute, row = _evaluator(stocks, ind)

    curves, brokers, rows = {}, {}, {}
    for name in STRATEGIES:
        sigs = runner.baseline_signals(stocks) if name == "sma_cross" else rule_signals(ind, name)
        curves[name], brokers[name] = execute(name, sigs)
        rows[name] = row(curves[name], brokers[name].trades)

    # ランダム選択は運の影響が大きいので、複数回の平均と幅を対照にする
    runs = [execute("random", rule_signals(ind, "random", seed=s)) for s in range(RANDOM_RUNS)]
    rr = pd.DataFrame([row(c, b.trades) for c, b in runs])
    curves["random"] = pd.concat([c for c, _ in runs], axis=1).mean(axis=1)
    rows["random"] = rr.mean(numeric_only=True).to_dict()
    random_range = (rr["総リターン"].min(), rr["総リターン"].max())

    for label, c in benchmark_curves(ind, bench, dates).items():
        curves[label], rows[label] = c, row(c)
    table = pd.DataFrame(rows).T.rename_axis("戦略")
    return {"curves": curves, "table": table, "split": mid, "brokers": brokers,
            "random_range": random_range}


def main(argv=None):
    import sys
    from . import data, portfolio, universe
    con = data.connect()
    prices, flags = data.read_clean(con)
    stocks, bench = runner.split_benchmarks(prices, portfolio.extra_tickers(con, universe.load(con)))
    if "--variants" in (argv if argv is not None else sys.argv[1:]):
        r = run_variants(stocks, bench)
        pd.set_option("display.width", 200)
        fmt = {c: "{:+.1%}".format for c in ("総リターン", "最大DD", "前半(調整期)", "後半(未知)")}
        print(f"候補ルールの変更案（前半/後半の境目 {r['split']:%Y-%m-%d}）")
        print(r["table"][["総リターン", "最大DD", "前半(調整期)", "後半(未知)", "約定数", "勝率"]]
              .to_string(formatters=fmt, na_rep="—"))
        return
    r = run_all(stocks, bench)
    pd.set_option("display.width", 200)
    print(f"価格データの自動補正: {len(flags)}件\n{flags.to_string(index=False) if len(flags) else ''}")
    fmt = {c: "{:+.1%}".format for c in ("総リターン", "最大DD", "前半(調整期)", "後半(未知)")}
    print(f"評価期間 {r['curves']['sma_cross'].index[0]:%Y-%m-%d}〜{r['curves']['sma_cross'].index[-1]:%Y-%m-%d} "
          f"（前半/後半の境目 {r['split']:%Y-%m-%d}）")
    print(r["table"].to_string(formatters=fmt, na_rep="—"))
    lo, hi = r["random_range"]
    print(f"random は{RANDOM_RUNS}回の平均。総リターンの幅は {lo:+.1%}〜{hi:+.1%}")


if __name__ == "__main__":
    main()
