"""3つの仮想口座(gpt / claude / baseline)の構築。画面と日次ジョブで共有する。"""
import pandas as pd

from . import fetch, sim, store
from .ai_adapters import Signal
from .paper_broker import PaperBroker
from .signals import sma_cross_actions

INITIAL = 300_000
MODELS = ("gpt", "claude", "baseline")
BASELINE_YEN = 25_000  # 1回あたりの買付額（損失上限0.5%に収まる大きさ）


def baseline_signals(prices: pd.DataFrame) -> list[Signal]:
    out = []
    for t, g in prices.groupby("ticker"):
        g = g.sort_values("date").reset_index(drop=True)
        acts = sma_cross_actions(g["close"])
        for i in acts.index[acts != "hold"]:
            out.append(Signal("baseline", g["date"].iloc[i].strftime("%Y-%m-%d"), t, acts[i], 0.5,
                              "移動平均クロス", "AIなし比較用",
                              qty=max(1, int(BASELINE_YEN // g["close"].iloc[i]))))
    return out


def split_benchmarks(prices: pd.DataFrame, exclude=frozenset()):
    """(売買対象の株価, 市場指標の株価)。指標と、excludeに挙げた銘柄（持ち株のみの銘柄）は売買しない。"""
    is_bench = prices["ticker"].isin(fetch.BENCHMARKS)
    skip = is_bench | prices["ticker"].isin(exclude)
    return prices[~skip].reset_index(drop=True), prices[is_bench]


def build_accounts(con, prices: pd.DataFrame):
    """戻り値: (accounts={model: (broker, 資産推移)}, signals={model: [Signal]})"""
    accounts, sigs = {}, {}
    for m in MODELS:
        sigs[m] = baseline_signals(prices) if m == "baseline" else store.load_signals(con, m)
        broker = PaperBroker(m, INITIAL)
        accounts[m] = (broker, sim.run(broker, prices, sigs[m]))
    return accounts, sigs
