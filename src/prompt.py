"""AIに渡す依頼文の生成。GPTとClaudeで同一のテンプレートを使い、口座状況だけモデルごとに変える。"""
import hashlib

import pandas as pd

from .market import MKT_MIN

TEMPLATE_V1 = """あなたは日本株の分析者です。以下のデータだけを根拠に、翌営業日の売買判断を出してください。
これは仮想売買の研究用で、実際の投資助言ではありません。データにない情報は使わないでください。

【制約】資金{initial:,}円 / 1銘柄は総資産の20%以下 / 1回の想定損失は総資産の0.5%以下 /
1日の新規購入は総資産の20%以下 / 信用・空売り禁止。売買は1株単位。
sellは保有している銘柄だけ有効。自信がなければ hold(見送り)にしてください。
下の「候補銘柄」と「保有銘柄」だけを判断対象にしてください(他の銘柄は出力不要)。

【出力】JSON配列のみ(説明文なし)。各要素:
{{"model": "{model}", "as_of": "{as_of}", "ticker": "<銘柄コード>", "action": "buy|sell|hold",
 "confidence": 0〜1の主観スコア(確率ではない), "reason": "根拠", "risk_notes": "リスク",
 "qty": 株数(整数。holdは0), "stop_price": 損切り価格(buyのとき必須)}}

【判断日】{as_of}（この日の終値までのデータ。約定は翌営業日の始値）

【市場全体】
{index_text}
{breadth_text}

{weak_text}【業種別の動き（20日騰落の降順）】
{sector_text}

【あなたの口座】総資産 {equity:,.0f}円 / 現金 {cash:,.0f}円
保有: {holdings}

【候補銘柄（ルールで機械的に絞った一覧。絞り込みの理由は reason）】
{cand_text}

【適時開示（東証TDnetの題名のみ。保有・候補銘柄について、前営業日15:30〜判断日の21:30までの分）】
本文は含みません。題名から読み取れる事実だけを根拠にし、内容を推測で補わないでください。
{news_text}
"""


START_V2 = "2026-10-29"  # この判断日から、AIが一覧から銘柄を自分で選ぶ依頼文にする（それ以前はV1）


def _v2(t: str) -> str:
    old_scope = "下の「候補銘柄」と「保有銘柄」だけを判断対象にしてください(他の銘柄は出力不要)。"
    old_head = "【候補銘柄（ルールで機械的に絞った一覧。絞り込みの理由は reason）】"
    assert old_scope in t and old_head in t
    return t.replace(old_scope,
                     "下の「候補銘柄」（機械的に抽出した一覧。保有銘柄を含む）の中から、買い・売りたい銘柄を自分で選んでください。\n"
                     "一覧にない銘柄は出力しないでください。保有銘柄は、買い・売り・見送りのいずれかを必ず出してください。\n"
                     "買い・売りがなく見送る日は、代表として1件だけ hold を出し、reason に全体の判断を書いてください。"
                     ).replace(old_head,
                               "【候補銘柄（複数の抽出条件で機械的に集めた一覧。該当した条件は reason。"
                               "抽出条件に有効性の保証はありません）】")


TEMPLATE = _v2(TEMPLATE_V1)  # 最新の依頼文


def template(as_of: str | None = None) -> str:
    """判断日に使う依頼文。as_ofがSTART_V2より前なら旧版（その日の判断と同じ条件に揃えるため）。"""
    return TEMPLATE_V1 if as_of is not None and as_of < START_V2 else TEMPLATE


def version(as_of: str | None = None) -> str:
    """依頼文の版。テンプレートの文面から自動で決まる（変えれば版も変わるので、記録し忘れない）。"""
    return hashlib.sha1(template(as_of).encode("utf-8")).hexdigest()[:8]


def _pct(x) -> str:
    return "—" if pd.isna(x) else f"{x:+.1%}"


def build_prompt(*, model, as_of, initial, equity, cash, positions, index_df, breadth, sectors, cands,
                 news=None) -> str:
    """positions: {ticker: (qty, 評価価格)}。news: src.news.for_prompt の結果（なければ空扱い）。"""
    news_text = "\n".join(f"- {r.ticker} {r.name} {r.date[5:].replace('-', '/')} {r.time}: {r.title}"
                          for r in news.itertuples()) if news is not None and len(news) else "（該当する開示なし）"
    index_text = "\n".join(f"- {r.指標}: {r.値:,.1f} （本日{_pct(r.本日)} / 5日{_pct(r.五日)} / 20日{_pct(r.二十日)}）"
                           for r in index_df.itertuples()) or "（指標データなし）"
    b = breadth
    breadth_text = (f"- 対象{b['銘柄数']}銘柄: 本日上昇 {b['本日上昇']:.0%} / 20日線の上 {b['20日線の上']:.0%} / "
                    f"60日線の上 {b['60日線の上']:.0%} / 20日高値更新 {b['20日高値更新']}銘柄 / "
                    f"平均騰落 本日{_pct(b['本日平均騰落'])}・20日{_pct(b['20日平均騰落'])}") if b else ""
    sector_text = "\n".join(f"- {r.業種}（{r.銘柄数}銘柄）: 本日{_pct(r.本日)} / 5日{_pct(r.五日)} / 20日{_pct(r.二十日)}"
                            for r in sectors.itertuples()) or "（なし）"
    weak_text = ""
    if b and b["60日線の上"] < MKT_MIN:
        weak_text = (f"【地合いの注意】60日線の上にある銘柄は{b['60日線の上']:.0%}（{MKT_MIN:.0%}未満）です。"
                     "過去約1年の検証では、この水準の日に「上昇基調の押し目」で買うと、成績が大きく悪化しました"
                     "（地合いが弱い日は買わないルールにすると、下落局面の損失が小さくなりました）。"
                     "ただし1つの相場だけの検証で、確実ではありません。新規の買いを控える判断も選択肢です。\n\n")
    hold = ", ".join(f"{t}×{q}株(評価単価{p:,.0f})" for t, (q, p) in positions.items()) or "なし"
    lines = ["ticker,name,sector,close,ret1,ret5,ret20,vs_sma20,vs_sma60,vol_ratio,vs_high60,reason"]
    for r in cands.itertuples():
        lines.append(",".join([r.ticker, str(getattr(r, "name", "")), str(getattr(r, "sector33", "")),
                               f"{r.close:.1f}", f"{r.ret1:+.3f}", f"{r.ret5:+.3f}", f"{r.ret20:+.3f}",
                               f"{r.gap20:+.3f}", f"{r.gap60:+.3f}", f"{r.vol_ratio:.2f}",
                               f"{r.dist_high60:+.3f}", r.reason]))
    return template(as_of).format(initial=initial, model=model, as_of=as_of, index_text=index_text,
                           breadth_text=breadth_text, weak_text=weak_text, sector_text=sector_text, equity=equity,
                           cash=cash, holdings=hold, cand_text="\n".join(lines), news_text=news_text)


