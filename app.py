"""GPT × Claude 日本株AIトレード研究所 ポータル（仮想売買のみ・実注文なし）

状況タブは30秒ごとに自動更新される。データはすべてローカル（SQLite）。
"""
from datetime import datetime

import pandas as pd
import streamlit as st

from src import (alert, backtest, data, fetch, market, metrics, news, portfolio, prompt, request, runner, status, store, ui,
                 universe)
from src import schedule
from src.ai_adapters import InvalidSignal, Signal, check_deadline, check_not_early, parse_signals
from src.runner import INITIAL, MODELS
from src.update import LOG

st.set_page_config(page_title="日本株AIトレード研究所", layout="wide")

SAMPLE = "sample/sample_prices.csv"
REFRESH = 30  # 秒


# ---- データ読み込み・計算 ---------------------------------------------------
@st.cache_resource(ttl=60, show_spinner=False)  # 口座オブジェクトはpickleできないため（再読み込みでクラスが変わる）
def compute(_stamp):
    """_stamp(DBの更新時刻)が変わるか60秒経つと再計算。"""
    con = data.connect()
    prices, flags = data.read_clean(con)
    if len(prices):
        source = "自動取得(yfinance)"
    else:
        prices, source = data.load_csv(SAMPLE), "架空サンプル"
    uni = universe.load(con)
    all_stocks, _ = runner.split_benchmarks(prices)  # 持ち株の評価用（TOPIX500外の保有銘柄も含む）
    prices, bench = runner.split_benchmarks(prices, portfolio.extra_tickers(con, uni))  # 指標・持ち株のみの銘柄は売買対象外
    accounts, sigs = runner.build_accounts(con, prices)
    store.ensure_ledger(con)
    ledger = {"trades": con.execute("SELECT COUNT(*) FROM trades").fetchone()[0],
              "days": con.execute("SELECT COUNT(DISTINCT date) FROM equity_daily").fetchone()[0],
              "last": con.execute("SELECT MAX(recorded_at) FROM trades").fetchone()[0],
              "mismatch": store.ledger_mismatches(con, accounts)}
    holdings = portfolio.load(con)
    con.close()
    dates = pd.DatetimeIndex(sorted(prices["date"].unique()))
    rel = backtest.benchmark_curves(backtest.indicators(prices), bench, dates, initial=1.0)  # 初日=1.0の指数
    return {"prices": prices, "bench": bench, "universe": uni, "source": source, "flags": flags,
            "bench_rel": rel,
            "snap": market.snapshot(prices, uni), "accounts": accounts, "signals": sigs,
            "ledger": ledger, "holdings": holdings,
            "snap_all": market.snapshot(all_stocks, uni)}


def stamp():
    p = data.DB_PATH
    return p.stat().st_mtime_ns if p.exists() else 0


def pct100(df, cols):
    """表示用: 小数の騰落率を%値(×100)に直したコピーを返す。"""
    out = df.copy()
    for c in cols:
        out[c] = out[c] * 100
    return out


def last_prices(prices):
    return {t: float(g["close"].iloc[-1]) for t, g in prices.groupby("ticker")}


def equity_series(c):
    """比較グラフ用の系列。起点は GPT/Claude の最初の判断日で、指数も同日に初期資金へ揃える。"""
    ai_days = [s.as_of for m in ("gpt", "claude") for s in c["signals"][m]]
    start = pd.Timestamp(min(ai_days)) if ai_days else c["prices"]["date"].min()
    series = {}
    for m, (_, curve) in c["accounts"].items():
        eq = curve[curve["date"] >= start].set_index("date")["equity"]
        series[m] = INITIAL * eq / eq.iloc[0]  # baselineは以前から動いているので、起点以降の成績だけを比べる
    for label, rel in c["bench_rel"].items():
        rel = rel[rel.index >= start]
        series[label] = INITIAL * rel / rel.iloc[0]
    return start, series


def show_equity(c, key):
    """比較グラフ。データが1日分しかないときは、線を引けないので理由を示す。keyは複数タブで同じ図を出すための識別子。"""
    start, series = equity_series(c)
    if max(len(s) for s in series.values()) < 2:
        ui.note(st, f"{start:%Y-%m-%d} の判断が翌営業日に約定すると、ここに線が表示されます。"
                    "次の株価取得は、状況タブの「次回の自動取得」の時刻です。")
    else:
        st.plotly_chart(ui.line_chart(series), width="stretch", key=key)
    st.caption(f"起点 {start:%Y-%m-%d}（AIの最初の判断日）。全口座と指数を同日に{INITIAL:,}円へ揃え、"
               "指数・全銘柄保有は点線。")


# ---- タブ1: 状況 -----------------------------------------------------------
@st.fragment(run_every=REFRESH)
def tab_status():
    c = compute(stamp())
    prices = c["prices"]
    latest = prices["date"].max()
    lag = status.business_lag(latest, schedule.expected_latest())
    label, sev = status.freshness(lag)
    log_lines = LOG.read_text(encoding="utf-8").splitlines() if LOG.exists() else []
    lg = status.parse_log(log_lines)
    task = status.task_info()

    if c["source"] == "架空サンプル":
        st.warning("まだ株価を取得していないため、架空のサンプルデータを表示しています。")

    nxt = status.parse_dt(task["next"]) if task else None
    task_bad = bool(task) and task["result"] not in (0, 267009, 267011)  # 実行中/未実行は除外
    ui.strip(st, [
        {"label": "データの鮮度", "value": label, "sub": f"最新日 {latest:%Y-%m-%d}", "level": sev},
        {"label": "直近の取得", "value": "正常" if lg["healthy"] else "要確認",
         "sub": f"{lg['last_ok']:%m/%d %H:%M}" if lg["last_ok"] else "記録なし",
         "level": "ok" if lg["healthy"] else "bad"},
        {"label": "次回の自動取得", "value": f"{nxt:%m/%d %H:%M}" if nxt else "未設定",
         "sub": (f"結果コード {task['result']}" if task_bad else f"タスク {task['state']}") if task
         else "タスク情報なし", "level": "bad" if task_bad else ("ok" if nxt else "warn")},
        {"label": "実注文", "value": "無効", "sub": "仮想売買のみ", "level": "ok"},
    ])
    if lg["last_err"] and not lg["healthy"]:
        st.error(f"取得に失敗しています（{lg['last_err']:%m/%d %H:%M}）。「更新ログ・ルール」タブで内容を確認してください。")

    # AI判断の状況: 判断待ちだけを強調する
    st.subheader("今日のAI判断")
    latest_s = latest.strftime("%Y-%m-%d")
    items = []
    for m in MODELS:
        today = [s for s in c["signals"][m] if s.as_of == latest_s]
        if m == "baseline":
            items.append({"name": "baseline", "state": "自動", "level": "ok",
                          "detail": f"AIなしの比較用。{latest_s} の売買シグナル {len(today)}件"})
        elif today:
            buys = sum(s.action == "buy" for s in today)
            items.append({"name": m, "state": "登録済み", "level": "ok",
                          "detail": f"{latest_s} 分 {len(today)}件（買い {buys}件）"})
        else:
            items.append({"name": m, "state": "判断待ち", "level": "warn",
                          "detail": f"{latest_s} 分が未登録。「AIへの依頼・登録」タブで登録します"})
    ui.rows(st, items)

    # 比較グラフ（この画面の主役）
    st.subheader("資産の推移")
    show_equity(c, "equity_status")

    # 市場の概況（詳細は「市場全体」タブ）
    st.subheader("市場の概況")
    br = market.breadth(c["snap"])
    idx = market.index_table(c["bench"], fetch.BENCHMARKS)
    cells = [{"label": r.指標, "value": f"{r.値:,.1f}", "sub": f"本日 {r.本日:+.2%}",
              "tone": "up" if r.本日 >= 0 else "down"} for r in idx.itertuples()]
    if br:
        cells += [{"label": "20日線の上にある銘柄", "value": f"{br['20日線の上']:.0%}", "sub": f"本日上昇 {br['本日上昇']:.0%}"},
                  {"label": "対象銘柄数", "value": f"{br['銘柄数']}", "sub": "TOPIX500" if len(c["universe"]) else "サンプル"}]
    ui.strip(st, cells)
    st.caption(f"表示更新 {datetime.now():%H:%M:%S}（{REFRESH}秒ごとに自動更新）。上昇は朱、下落は藍。")


# ---- タブ: 市場全体 ----------------------------------------------------------
@st.fragment(run_every=REFRESH)
def tab_market():
    c = compute(stamp())
    snap = c["snap"]
    if snap.empty:
        ui.note(st, "市場データがまだありません。株価の自動取得が済むと表示されます。")
        return
    st.caption(f"{snap['date'].max():%Y-%m-%d} 終値ベース／対象 {len(snap)}銘柄（JPX公式のTOPIX500）")
    idx = market.index_table(c["bench"], fetch.BENCHMARKS)
    ui.strip(st, [{"label": r.指標, "value": f"{r.値:,.1f}",
                   "sub": f"本日 {r.本日:+.2%}／5日 {r.五日:+.2%}／20日 {r.二十日:+.2%}",
                   "tone": "up" if r.本日 >= 0 else "down"} for r in idx.itertuples()])

    b = market.breadth(snap)
    ui.strip(st, [
        {"label": "本日上昇した銘柄", "value": f"{b['本日上昇']:.0%}", "sub": f"平均 {b['本日平均騰落']:+.2%}"},
        {"label": "20日線の上にある銘柄", "value": f"{b['20日線の上']:.0%}", "sub": "短期の地合い"},
        {"label": "60日線の上にある銘柄", "value": f"{b['60日線の上']:.0%}", "sub": "中期の地合い"},
        {"label": "20日高値を更新", "value": f"{b['20日高値更新']}銘柄", "sub": f"20日平均 {b['20日平均騰落']:+.1%}"},
    ])

    sec = market.sector_table(snap)
    if len(sec):
        st.subheader("業種別の20日騰落")
        st.plotly_chart(ui.sector_bars(sec["業種"], sec["二十日"], height=max(300, 22 * len(sec))), width="stretch")
        st.caption("朱は上昇、藍は下落（日本の相場の慣習）。")

    names = {"ticker": "銘柄", "name": "銘柄名", "sector33": "業種", "close": "終値",
             "ret1": "本日", "ret5": "5日", "ret20": "20日", "reason": "絞り込みの理由"}
    rets = ("ret1", "ret5", "ret20")
    cfg = {names[k]: st.column_config.NumberColumn(format="%+.2f%%") for k in rets} \
        | {"終値": st.column_config.NumberColumn(format="localized")}

    def table(df):
        cols = [x for x in names if x in df]
        return pct100(df[cols], [r for r in rets if r in cols]).rename(columns=names)

    g, l = st.columns(2)
    g.subheader("本日の上昇上位")
    g.dataframe(table(snap.nlargest(10, "ret1")), hide_index=True, column_config=cfg)
    l.subheader("本日の下落上位")
    l.dataframe(table(snap.nsmallest(10, "ret1")), hide_index=True, column_config=cfg)

    st.subheader("AIに見せる候補")
    st.dataframe(table(market.candidates(snap)), hide_index=True, width="stretch", column_config=cfg)
    st.caption("ルールで機械的に絞った銘柄です。売買代金が上位半分・単価3万円以下から、「上昇基調の押し目」を"
               "抽出しています（「高値更新＋出来高増」は過去検証で成績が悪く外しました）。"
               "保有銘柄は依頼文で別に追加されます。")
    if b["60日線の上"] < market.MKT_MIN:
        ui.note(st, f"地合いが弱い日です（60日線の上にある銘柄 {b['60日線の上']:.0%}、警告線 {market.MKT_MIN:.0%}）。"
                    "過去検証では、こうした日の押し目買いは成績が悪化しました。依頼文にも同じ警告が入ります。", level="warn")


# ---- タブ2: 成績比較 ---------------------------------------------------------
@st.fragment(run_every=REFRESH)
def tab_compare():
    c = compute(stamp())
    last = last_prices(c["prices"])
    show_equity(c, "equity_compare")

    start, series = equity_series(c)   # 総資産・損益・最大DDは、起点で揃えた値で比べる
    rows = []
    for m, (b, curve) in c["accounts"].items():
        wr, n = metrics.win_rate(b.trades)
        assert abs(b.equity(last) - curve["equity"].iloc[-1]) < 1e-6  # 現金+評価額=総資産
        s = series[m]
        rows.append({"口座": m, "総資産": f"{s.iloc[-1]:,.0f}円", "損益": f"{s.iloc[-1] - INITIAL:+,.0f}円",
                     "最大ドローダウン": f"{metrics.max_drawdown(s):.2%}",
                     "約定数": len(b.trades), "拒否数": len(b.rejections),
                     "勝率": f"{wr:.0%}（売却{n}回）" if wr is not None else "—"})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption(f"総資産・損益・最大ドローダウンは起点 {start:%Y-%m-%d} 以降。約定数・拒否数・勝率は開設以来の累計"
               "（baselineは以前から動いているため多い）。売却回数が少ないうちの勝率・損益は偶然の影響が大きく、"
               "優劣の根拠になりません。手数料0.1%・スリッページ0.1%を仮定。")


# ---- タブ: 自分の持ち株 -----------------------------------------------------
def _signed_color(v):
    """日本の慣習: プラスは朱、マイナスは藍。"""
    if pd.isna(v) or v == 0:
        return ""
    return f"color: {ui.UP if v > 0 else ui.DOWN}"


@st.fragment(run_every=REFRESH)
def tab_portfolio():
    c = compute(stamp())
    v = portfolio.valuation(c["holdings"], c["snap_all"])
    ui.note(st, "ここは仮想売買とは別の、あなた自身の持ち株の記録です。AIの判断や仮想口座には使わず、注文機能もありません。"
                "データはこのPCの lab.db に保存されます。ただし「持ち株の評価」エージェントを使うと、株数・取得単価を含む要約がClaudeに送られます"
                "（使わなければ外部には送りません）。")
    if v.empty:
        ui.note(st, "持ち株がまだ登録されていません。下の「持ち株を追加・更新・削除」から登録します。")
    else:
        t = portfolio.totals(v)
        ui.strip(st, [
            {"label": "評価額（終値ベース）", "value": f"{t['value']:,.0f}円", "sub": f"取得原価 {t['cost']:,.0f}円"},
            {"label": "評価損益", "value": f"{t['pnl']:+,.0f}円", "sub": f"{t['pnl_pct']:+.2%}",
             "tone": "up" if t["pnl"] >= 0 else "down"},
            {"label": "保有銘柄数", "value": f"{t['n']}銘柄",
             "sub": f"価格を取得できない銘柄 {t['unpriced']}" if t["unpriced"] else "すべて価格を取得済み"},
        ])
        show = v.rename(columns={"ticker": "銘柄", "name": "銘柄名", "sector33": "業種", "qty": "株数",
                                 "avg_cost": "平均取得単価", "close": "終値", "value": "評価額", "pnl": "評価損益",
                                 "pnl_pct": "損益率", "weight": "構成比", "ret1": "本日", "ret20": "20日"})
        cols = ["銘柄", "銘柄名", "業種", "株数", "平均取得単価", "終値", "評価額", "評価損益", "損益率", "構成比", "本日", "20日"]
        styled = show[cols].style.format({
            "株数": "{:,}", "平均取得単価": "{:,.0f}", "終値": "{:,.1f}", "評価額": "{:,.0f}", "評価損益": "{:+,.0f}",
            "損益率": "{:+.2%}", "構成比": "{:.1%}", "本日": "{:+.2%}", "20日": "{:+.2%}"}, na_rep="—"
        ).map(_signed_color, subset=["評価損益", "損益率", "本日", "20日"])
        st.dataframe(styled, hide_index=True, width="stretch")

        sec = v.groupby("sector33")["value"].sum().sort_values(ascending=False)
        st.subheader("業種の偏り")
        ui.rows(st, [{"name": name or "（業種不明）", "state": f"{val / sec.sum():.0%}", "level": "ok",
                      "detail": f"評価額 {val:,.0f}円"} for name, val in sec.items()])
        st.caption("評価は取得済みの終値ベースで、証券会社の画面とは一致しません。配当・手数料・税金は含みません。"
                   "平均取得単価は入力値です。")

    reviews = sorted((LOG.parent / "reviews").glob("review_*.md")) if (LOG.parent / "reviews").exists() else []
    with st.expander("AIによる持ち株の評価（最新）", expanded=bool(reviews)):
        if reviews:
            st.caption(f"{reviews[-1].name}。「持ち株の評価エージェント」が作成。投資助言ではなく情報の整理です。")
            st.markdown(reviews[-1].read_text(encoding="utf-8"))
        else:
            st.write("まだ評価がありません。Claude Code で「holdings-reviewer で持ち株を評価して」と依頼すると作成されます。")

    with st.expander("持ち株を追加・更新・削除"):
        with st.form("holding_upsert", clear_on_submit=True):
            a, b, d = st.columns(3)
            ticker = a.text_input("銘柄コード", placeholder="例 8035")
            qty = b.number_input("株数", min_value=0, step=1, value=0)
            cost = d.number_input("平均取得単価（円）", min_value=0.0, step=1.0, value=0.0)
            if st.form_submit_button("保存（同じ銘柄は上書き）"):
                try:
                    con = data.connect()
                    portfolio.upsert(con, ticker, int(qty), float(cost))
                    con.close()
                    compute.clear()
                    st.rerun()
                except portfolio.InvalidHolding as e:
                    st.error(f"保存しません: {e}")
        if not c["holdings"].empty:
            who = st.selectbox("削除する銘柄", c["holdings"]["ticker"])
            if st.button("この銘柄を削除"):
                con = data.connect()
                portfolio.remove(con, who)
                con.close()
                compute.clear()
                st.rerun()
        st.caption("TOPIX500以外の銘柄も登録できます。価格は次回の自動取得（平日21:30）から取得され、"
                   "それまでは価格なしと表示されます。")


# ---- タブ: 過去データでの検証（バックテスト）---------------------------------
@st.cache_data(ttl=3600, show_spinner="過去データで検証中（初回は約20秒）…")
def run_backtest(_stamp):
    con = data.connect()
    prices, _ = data.read_clean(con)
    extra = portfolio.extra_tickers(con, universe.load(con))
    con.close()
    stocks, bench = runner.split_benchmarks(prices, extra)
    return backtest.run_all(stocks, bench)


@st.cache_data(ttl=3600, show_spinner="変更案を検証中（約20秒）…")
def run_variants(_stamp):
    con = data.connect()
    prices, _ = data.read_clean(con)
    extra = portfolio.extra_tickers(con, universe.load(con))
    con.close()
    stocks, bench = runner.split_benchmarks(prices, extra)
    return backtest.run_variants(stocks, bench)


def tab_backtest():
    c = compute(stamp())
    if c["source"] == "架空サンプル":
        ui.note(st, "サンプルデータでは検証できません。株価の自動取得が済んでから実行してください。")
        return
    if not st.session_state.get("bt_ready"):  # 重い計算なので、押したときだけ実行（結果は1時間キャッシュ）
        if st.button("過去検証を実行（初回は約20秒）"):
            st.session_state["bt_ready"] = True
            st.rerun()
        return
    r = run_backtest(stamp())
    t = r["table"].copy()
    first = r["curves"]["sma_cross"].index[0]
    last = r["curves"]["sma_cross"].index[-1]
    st.markdown(f"**AIなしのルール戦略を、過去約1年の株価で検証**（評価期間 {first:%Y-%m-%d}〜{last:%Y-%m-%d}、"
                f"前半/後半の境目 {r['split']:%Y-%m-%d}）。GPT/Claudeの判断は過去分がないため、ここには含みません。")
    fig = ui.line_chart(r["curves"], height=400)
    fig.add_vline(x=r["split"], line_dash="dash", line_color=ui.MUTED,
                  annotation_text="ここから後半（未知データ）", annotation_font_color=ui.MUTED)
    st.plotly_chart(fig, width="stretch")

    pct = lambda v: f"{v:+.1%}"
    shown = pd.DataFrame({
        "総リターン": t["総リターン"].map(pct), "最大ドローダウン": t["最大DD"].map(pct),
        "前半（調整期）": t["前半(調整期)"].map(pct), "後半（未知）": t["後半(未知)"].map(pct),
        "約定数": [f"{int(n):,}" if n else "—" for n in t["約定数"]],
        "勝率": [f"{w:.0%}" if pd.notna(w) else "—" for w in t["勝率"]]})
    st.dataframe(shown, width="stretch")
    lo, hi = r["random_range"]
    st.caption(f"random は{backtest.RANDOM_RUNS}回の平均で、総リターンの幅は {lo:+.1%}〜{hi:+.1%}。"
               "運だけでこの幅が出る、という基準線です。")
    ui.note(st, "結果の読み方", [
        "期間が約1年・1つの相場だけで、優劣の根拠にはなりません。前半と後半で成績が逆転する戦略は信頼できません。",
        "銘柄は現在のTOPIX500です。過去に除外された銘柄が含まれない（生存者バイアス）ため、成績は甘めに出ます。",
        "戦略ごとに投入する資金（ポジションの持ち方）が違うため、指数との単純比較はできません。",
        "ルールは事前に固定し、結果を見てのパラメータ調整はしていません（調整すると過去に合わせただけになります）。"],
        level="warn")
    with st.expander("候補ルールの見直し（変更案の比較）"):
        st.markdown("結果を見る前に決めた6案だけを、同じ条件で比べます。後半（未知データ）だけが良い案は採用しません。"
                    "採用したのは `pullback_mkt`（60日線の上にある銘柄が50%未満の日は買わない）です。")
        if st.button("変更案を比較する（約20秒）"):
            st.session_state["var_ready"] = True
        if st.session_state.get("var_ready"):
            vt = run_variants(stamp())["table"]
            fmt = lambda v: "—" if pd.isna(v) else f"{v:+.1%}"
            st.dataframe(pd.DataFrame({
                "総リターン": vt["総リターン"].map(fmt), "最大ドローダウン": vt["最大DD"].map(fmt),
                "前半": vt["前半(調整期)"].map(fmt), "後半（未知）": vt["後半(未知)"].map(fmt)}), width="stretch")
            st.caption("_mkt=地合いフィルター付き、_deep=5日の下げが深い順に選ぶ。6案から選ぶため選び方の偏りがあり、"
                       "約1年・1つの相場だけの結果です。")
    with st.expander("戦略の定義"):
        st.markdown(
            f"- **sma_cross**: 5日線が20日線を上抜けで買い、下抜けで売り。1回{backtest.YEN:,}円。\n"
            f"- **pullback**: 売買代金が上位半分・単価3万円以下で、上昇基調(20日線>60日線、終値>60日線)かつ5日下落の銘柄を、"
            f"20日騰落の大きい順に毎日{backtest.TOP_N}銘柄買い、{backtest.HOLD_DAYS}営業日後に売り。\n"
            f"- **momentum**: 20日高値更新かつ出来高が平均の1.3倍以上の銘柄を、出来高比の大きい順に毎日{backtest.TOP_N}銘柄買い、"
            f"{backtest.HOLD_DAYS}営業日後に売り。\n"
            f"- **random**: 同じ流動性条件の銘柄から毎日{backtest.TOP_N}銘柄を無作為に買い、{backtest.HOLD_DAYS}営業日後に売り。\n"
            "- 約定は翌営業日の始値。手数料0.1%・スリッページ0.1%、資金・損失の制限は本番と同じ。")


# ---- タブ3: 口座詳細 ---------------------------------------------------------
@st.fragment(run_every=REFRESH)
def tab_accounts():
    c = compute(stamp())
    last = last_prices(c["prices"])
    lg = c["ledger"]
    if lg["mismatch"]:
        st.error(f"記録済みの約定と再計算が食い違っています（{len(lg['mismatch'])}件）。"
                 f"ルールやデータが変わった可能性があります: {', '.join(lg['mismatch'][:5])}")
    else:
        st.caption(f"取引履歴DB: 約定{lg['trades']}件を記録済み／日次資産{lg['days']}日分"
                   "（毎日の自動更新で追記。記録済みの約定は書き換わらない）")
    sub = st.tabs(list(c["accounts"]))
    for tab, (m, (b, _)) in zip(sub, c["accounts"].items()):
        with tab:
            x, y = st.columns(2)
            x.metric("現金", f"{b.cash:,.0f}円")
            y.metric("保有評価額", f"{sum(q * last[t] for t, q in b.positions.items()):,.0f}円")
            st.write("保有銘柄")
            st.dataframe(pd.DataFrame([{"銘柄": t, "株数": q, "評価額": round(q * last[t])}
                                       for t, q in b.positions.items()]), hide_index=True)
            st.write("取引履歴")
            st.dataframe(pd.DataFrame([t.__dict__ for t in b.trades]), hide_index=True)
            if b.rejections:
                st.write("拒否された注文（リスク制限などで見送り）")
                st.dataframe(pd.DataFrame([r.__dict__ for r in b.rejections]), hide_index=True)


# ---- タブ4: AIへの依頼・回答の登録 -------------------------------------------
def build_request(c, who):
    """(依頼文, 依頼文に載せた適時開示)。表示と登録時の記録で同じ関数を使い、内容を一致させる。"""
    con = data.connect()
    try:
        return request.build(who=who, prices=c["prices"], bench=c["bench"], snap=c["snap"],
                             broker=c["accounts"][who][0], con=con)
    finally:
        con.close()

def tab_ai():
    c = compute(stamp())
    prices = c["prices"]
    latest_s = prices["date"].max().strftime("%Y-%m-%d")
    st.markdown("**手順**: ① 下の依頼文をChatGPTとClaudeに貼る → ② 返ってきたJSONを下に貼って登録。"
                " 不正なJSONは登録されず、その日は見送りになります。")

    who = st.radio("依頼文の宛先", ["gpt", "claude"], horizontal=True,
                   help="市場データ・候補は同一。口座の現金と保有だけモデルごとに違います。")
    if st.button("適時開示を再取得"):
        con = data.connect()
        try:
            st.toast(f"新規{news.update(con, datetime.now().date())}件の開示を保存しました")
        except Exception as e:
            st.error(f"適時開示の取得に失敗: {e}")
        con.close()
    try:  # 情報の締め切り（判断日の21:30）前は依頼文を出さない。いつ聞いても同じ情報にするため
        check_not_early([Signal(who, latest_s, "-", "hold", 0, "", "")])
        text_prompt, used_news = build_request(c, who)
    except InvalidSignal as e:
        st.warning(str(e))
        text_prompt = None
    if text_prompt:
        with st.expander(f"① {who} に貼る依頼文（判断日 {latest_s} ／ 全文をコピー）", expanded=True):
            st.code(text_prompt, language="text")
        st.caption(f"適時開示 {len(used_news)}件を依頼文に載せています（{schedule.DAILY_RUN:%H:%M}までの分）。"
                   "登録すると、載せた開示も記録されます。")

    st.subheader("② 回答JSONの登録")
    with st.form("register", clear_on_submit=True):
        model = st.selectbox("どちらの回答？", ["gpt", "claude"])
        text = st.text_area("JSON", height=200)
        if st.form_submit_button("登録"):
            try:
                sigs = parse_signals(text, expected_model=model, known_tickers=set(prices["ticker"]))
                avail = set(zip(prices["ticker"], prices["date"].dt.strftime("%Y-%m-%d")))
                bad = [s for s in sigs if (s.ticker, s.as_of) not in avail]
                if bad:
                    raise InvalidSignal(f"データに存在しない日付です: {bad[0].ticker} {bad[0].as_of}")
                request.check_allowed(sigs, request.allowed_tickers(
                    latest_s, c["snap"], list(c["accounts"][model][0].positions)))
                check_not_early(sigs)
                check_deadline(sigs, prices["date"].max())
                con = data.connect()
                n = store.save_signals(con, sigs, prompt.version(latest_s), prompt.template(latest_s), source="portal")
                _, reg_news = build_request(c, model)
                news.record_used(con, model, latest_s, reg_news["url"])
                con.close()
                compute.clear()
                st.success(f"{n}件登録（重複{len(sigs) - n}件は無視）")
            except InvalidSignal as e:
                st.error(f"登録しません（見送り）: {e}")

    con = data.connect()
    versions = store.prompt_version_summary(con)
    sources = store.source_summary(con)
    con.close()
    st.caption(f"現在の依頼文の版: {prompt.version(latest_s)}。依頼文を変えると版が変わり、判断は登録時の版と一緒に記録されます"
               "（変更前後を分けて評価するため）。")
    if versions:
        st.dataframe(pd.DataFrame([(v or "未記録", (f or "")[:16].replace("T", " "), n) for v, f, n in versions],
                                  columns=["依頼文の版", "初めて使った日時(UTC)", "登録した判断の件数"]),
                     hide_index=True)

    if sources:
        st.caption("判断の出どころ: portal=人がこの画面に貼った / agent=判断用エージェントが登録 / 未記録=この記録を始める前")
        st.dataframe(pd.DataFrame([(m, s or "未記録", l or "", n) for m, s, l, n in sources],
                                  columns=["AI", "出どころ", "申告されたモデル名", "判断日の数"]), hide_index=True)

    st.subheader("登録済みの判断")
    rows = [s.__dict__ for m in ("gpt", "claude") for s in c["signals"][m]]
    st.dataframe(pd.DataFrame(rows).sort_values("as_of", ascending=False) if rows else pd.DataFrame(),
                 hide_index=True, width="stretch")


# ---- タブ: ニュース（AIが判断に使った開示） ------------------------------------
ACTION_JA = {"buy": "買い", "sell": "売り", "hold": "見送り"}


def tab_news():
    c = compute(stamp())
    con = data.connect()
    st.caption("AIの依頼文に載せた東証TDnetの適時開示（題名のみ）を、その判断と並べて表示します。"
               "AIに渡したのは題名だけで、本文は渡していません。")
    days = sorted({s.as_of for m in ("gpt", "claude") for s in c["signals"][m]}, reverse=True)
    if not days:
        st.info("まだAIの判断が登録されていません。")
    else:
        col1, col2 = st.columns(2)
        day = col1.selectbox("判断日", days)
        who = col2.radio("AI", ["gpt", "claude"], horizontal=True)
        sigs = [s for s in c["signals"][who] if s.as_of == day]
        used = news.used_for(con, who, day)
        if used.empty:
            ui.note(st, f"{who} の {day} の判断には、載せた開示の記録がありません"
                        "（開示を載せる機能の前の判断か、該当する開示がなかった日です）。")
        by_ticker = {t: g for t, g in used.groupby("ticker")}
        acts = [s for s in sigs if s.action != "hold"]
        shown = [s for s in sigs if s.ticker in by_ticker]
        st.write(f"判断 {len(sigs)}件（買い・売り {len(acts)}件）／ 開示が載った銘柄の判断 {len(shown)}件")
        for s in sorted(sigs, key=lambda s: (s.ticker not in by_ticker, s.action == "hold", s.ticker)):
            g = by_ticker.get(s.ticker)
            name = g["name"].iloc[0] if g is not None else dict(zip(c["universe"]["ticker"], c["universe"]["name"])).get(s.ticker, "") if len(c["universe"]) else ""
            with st.expander(f"{s.ticker} {name} — {ACTION_JA[s.action]}"
                             + (f"（{len(g)}件の開示）" if g is not None else "（開示なし）"), expanded=g is not None and s.action != "hold"):
                st.markdown(f"**理由**: {s.reason}\n\n**リスク**: {s.risk_notes}")
                if g is not None:
                    st.markdown("**AIに渡した開示**\n" + "\n".join(
                        f"- {r.date[5:].replace('-', '/')} {r.time} [{r.title}]({r.url})" for r in g.itertuples()))

    with st.expander("開示の一覧を見る（AIに渡したかどうかに関係なく、直近2週間）"):
        since = (datetime.now() - pd.Timedelta(days=14)).strftime("%Y-%m-%d")
        df = news.load(con, since)
        mine = set(c["holdings"]["ticker"]) if len(c["holdings"]) else set()
        scope = st.radio("範囲", ["個人の持ち株", "すべて"], horizontal=True)
        if scope == "個人の持ち株":
            df = df[df["ticker"].isin(mine)]
        q = st.text_input("検索（銘柄名・コード・題名）")
        if q:
            df = df[df[["ticker", "name", "title"]].apply(lambda s: s.str.contains(q, case=False, regex=False)).any(axis=1)]
        st.write(f"{len(df)}件")
        st.dataframe(df.head(500).rename(columns={"date": "日付", "time": "時刻", "ticker": "コード",
                                                 "name": "銘柄", "title": "題名", "url": "資料(PDF)"}),
                     hide_index=True, width="stretch",
                     column_config={"資料(PDF)": st.column_config.LinkColumn("資料(PDF)", display_text="開く")})
    con.close()

# ---- タブ5: ログ・ルール -----------------------------------------------------
@st.fragment(run_every=REFRESH)
def tab_log():
    st.write("更新ログ（新しい順・最新50行）")
    lines = LOG.read_text(encoding="utf-8").splitlines()[-50:][::-1] if LOG.exists() else []
    st.code("\n".join(lines) or "まだログがありません", language="text")
    uni = compute(stamp())["universe"]
    st.write(f"対象銘柄: JPX公式のTOPIX500（{len(uni)}銘柄、週1回更新）＋市場指標"
             if len(uni) else "対象銘柄: 一覧未取得（予備の watchlist.txt / サンプルを使用）")
    flags = compute(stamp())["flags"]
    st.write(f"価格データの自動補正（{len(flags)}件）")
    if len(flags):
        st.caption("株価の異常値の除去・分割の調整・原因不明の銘柄の除外。元のDBは書き換えず、分析時にだけ適用。")
        st.dataframe(flags.rename(columns={"ticker": "銘柄", "date": "日付", "kind": "処理", "detail": "内容"}),
                     hide_index=True, width="stretch")
    st.write("固定ルール")
    st.table(pd.DataFrame([
        ("初期資金", "各30万円"), ("1銘柄の配分", "総資産の20%以下"),
        ("1回の想定損失", "総資産の0.5%以下（損切り未指定は5%下を仮定）"),
        ("1日の新規購入", "総資産の20%以下"), ("信用・空売り", "禁止"),
        ("約定", "判断日の翌営業日の始値（1株単位の仮想約定）"),
        ("登録の締め切り", "判断日の21:30以降〜翌営業日（土日・祝日・年末年始を除く）の9:00まで。この時間外、または翌営業日の株価取得後は登録不可"),
        ("異常データ・不正JSON", "売買見送り"), ("実注文", "完全無効（経路なし）"),
    ], columns=["項目", "設定"]))


# ---- 画面 ------------------------------------------------------------------
ui.inject(st)
ui.masthead(st, "日本株AIトレード研究所",
            "GPTとClaudeの売買判断を、同じ条件の仮想口座で比べる研究ポータル。実注文や証券口座との連携はありません。")
try:
    compute(stamp())
except data.DataError as e:
    st.error(f"データ異常のため停止: {e}")
    st.stop()

def banner():
    """どのタブにいても気づけるよう、取得の問題をタブの上に出す。"""
    a = alert.read(data.DB_PATH.parent / "alert.json")
    if a:
        st.error(f"{a['message']}（{a['at'].replace('T', ' ')}）\n\n" + "\n".join(f"- {d}" for d in a["detail"]))
    lag = status.business_lag(compute(stamp())["prices"]["date"].max(), schedule.expected_latest())
    if lag >= 2 and not a:  # 実行されなかった場合（PCがoff・スリープ）はアラートが書かれないので、鮮度でも見る
        st.error(f"株価が{lag}営業日分更新されていません。PCの状態とタスクスケジューラを確認してください。")


banner()
t1, tp, tm, tn, t2, tb, t3, t4, t5 = st.tabs(
    ["状況", "持ち株", "市場全体", "ニュース", "成績比較", "過去検証", "口座詳細", "AIへの依頼・登録", "更新ログ・ルール"])
with t1:
    tab_status()
with tp:
    tab_portfolio()
with tm:
    tab_market()
with tn:
    tab_news()
with t2:
    tab_compare()
with tb:
    tab_backtest()
with t3:
    tab_accounts()
with t4:
    tab_ai()
with t5:
    tab_log()








