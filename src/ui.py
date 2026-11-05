"""ポータルの見た目。色・フォント・運転状況の帯・グラフの体裁をここに集める。

方針:
- 日本の相場の慣習に合わせ、上昇は朱、下落は藍。
- 正常は色を付けず、異常のときだけ色と記号を付ける（色だけに頼らず、記号と言葉も付ける）。
- 箱（カード）を並べず、罫線と余白で区切る。目立たせるのは比較グラフ1か所だけ。
"""
import html

import plotly.graph_objects as go

INK, PAPER, MUTED, RULE = "#1C2330", "#F2F4F7", "#5A6477", "#D3D9E2"
UP, DOWN, WARN = "#C4302B", "#2457A6", "#9A5B00"   # 上昇=朱 / 下落=藍 / 注意=琥珀
FONT = "'BIZ UDPGothic','Yu Gothic UI','Meiryo',sans-serif"

# 系列ごとの色。AIは判断の主役なので太く、指数類は細い点線の灰色系にして背景に退ける。
COLORS = {
    "gpt": "#6B4FBB", "claude": "#0F8A8A", "baseline": "#C9821A",
    "日経平均": "#5A6477", "TOPIX連動ETF": "#8A93A3", "全銘柄を等金額で保有": "#B3BAC6",
    "sma_cross": "#C9821A", "pullback": "#3E7CB1", "momentum": "#B5547C", "random": "#7A8294",
}
REFERENCE = ("日経平均", "TOPIX連動ETF", "全銘柄を等金額で保有")   # 参考線（細い点線）
GLYPH = {"ok": "●", "warn": "▲", "bad": "■"}

CSS = f"""
@import url('https://fonts.googleapis.com/css2?family=BIZ+UDPGothic:wght@400;700&display=swap');
:root {{ --ink:{INK}; --muted:{MUTED}; --rule:{RULE}; --up:{UP}; --down:{DOWN}; --warn:{WARN}; }}
.stApp, .stApp p, .stApp label, .stApp li, .stApp h1, .stApp h2, .stApp h3, .stApp h4,
.stApp [data-testid="stMetric"], .stApp [data-testid="stCaptionContainer"],
.stApp button[role="tab"], .stApp textarea, .stApp input {{ font-family:{FONT}; }}
.block-container {{ max-width:1180px; padding-top:2.2rem; }}
.stApp h2, .stApp h3 {{ font-weight:700; letter-spacing:.01em; }}
.stApp h3 {{ font-size:1.15rem; margin-top:1.6rem; }}

.mast h1 {{ font-size:2rem; font-weight:700; margin:0; letter-spacing:.02em; }}
.mast p {{ color:var(--muted); margin:.35rem 0 1.4rem; max-width:44rem; }}

button[role="tab"] {{ font-weight:700; color:var(--muted); }}
button[role="tab"][aria-selected="true"] {{ color:var(--ink); }}
[data-testid="stMetricValue"] {{ font-weight:700; font-variant-numeric:tabular-nums; }}

.strip {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(11.5rem,1fr));
  border-top:2px solid var(--ink); border-bottom:1px solid var(--rule); margin:.4rem 0 1.4rem; }}
.strip .cell {{ padding:.75rem 1rem .8rem 0; }}
.strip .cell + .cell {{ border-left:1px solid var(--rule); padding-left:1rem; }}
.strip .k {{ color:var(--muted); font-size:.85rem; }}
.strip .v {{ font-size:1.45rem; font-weight:700; line-height:1.35; font-variant-numeric:tabular-nums; }}
.strip .s {{ color:var(--muted); font-size:.85rem; font-variant-numeric:tabular-nums; }}
.strip .s.up {{ color:var(--up); }} .strip .s.down {{ color:var(--down); }}
.strip .v.warn, .rows .state.warn {{ color:var(--warn); }}
.strip .v.bad, .rows .state.bad {{ color:var(--up); }}
.g {{ font-size:.7em; margin-right:.4em; vertical-align:.1em; }}

.rows {{ margin:.2rem 0 1.2rem; }}
.rows .row {{ display:grid; grid-template-columns:7rem 10rem 1fr; gap:1rem; padding:.55rem 0;
  border-bottom:1px solid var(--rule); align-items:baseline; }}
.rows .state {{ font-weight:700; }}
.rows .state.ok {{ color:var(--muted); font-weight:400; }}
.rows .detail {{ color:var(--muted); }}
@media (max-width:640px) {{ .rows .row {{ grid-template-columns:1fr; gap:.1rem; }} }}

.note {{ border-left:3px solid var(--rule); padding:.15rem 0 .15rem 1rem; margin:.6rem 0 1rem;
  color:var(--muted); max-width:48rem; }}
.note.warn {{ border-left-color:var(--warn); }}
.note b {{ color:var(--ink); }}
.note ul {{ margin:.3rem 0 0; padding-left:1.1rem; }}
.note li {{ margin:.2rem 0; }}
"""


def inject(st):
    st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)


def masthead(st, title: str, lead: str):
    st.markdown(f'<div class="mast"><h1>{html.escape(title)}</h1><p>{html.escape(lead)}</p></div>',
                unsafe_allow_html=True)


def strip_html(cells: list[dict]) -> str:
    """cells: [{label, value, sub, level(ok/warn/bad/None), tone(up/down/None)}] を1本の帯にする。"""
    out = []
    for c in cells:
        lv = c.get("level")
        glyph = f'<span class="g" aria-hidden="true">{GLYPH[lv]}</span>' if lv in ("warn", "bad") else ""
        out.append(
            '<div class="cell">'
            f'<div class="k">{html.escape(c["label"])}</div>'
            f'<div class="v {lv or ""}">{glyph}{html.escape(str(c["value"]))}</div>'
            f'<div class="s {c.get("tone") or ""}">{html.escape(str(c.get("sub", "")))}</div></div>')
    return f'<div class="strip">{"".join(out)}</div>'


def strip(st, cells):
    st.markdown(strip_html(cells), unsafe_allow_html=True)


def rows_html(rows: list[dict]) -> str:
    """rows: [{name, state, level(ok/warn/bad), detail}]。正常は目立たせず、判断待ちだけ強調する。"""
    out = []
    for r in rows:
        lv = r["level"]
        glyph = f'<span class="g" aria-hidden="true">{GLYPH[lv]}</span>' if lv != "ok" else ""
        out.append('<div class="row">'
                   f'<div>{html.escape(r["name"])}</div>'
                   f'<div class="state {lv}">{glyph}{html.escape(r["state"])}</div>'
                   f'<div class="detail">{html.escape(r["detail"])}</div></div>')
    return f'<div class="rows">{"".join(out)}</div>'


def rows(st, items):
    st.markdown(rows_html(items), unsafe_allow_html=True)


def note_html(text: str, items: list[str] = (), level: str = "info") -> str:
    """罫線つきの注記。通常は灰色、読み違えると困る注意だけ琥珀色の線にする。"""
    lis = "".join(f"<li>{html.escape(i)}</li>" for i in items)
    body = f"<ul>{lis}</ul>" if lis else ""
    return f'<div class="note {"warn" if level == "warn" else ""}"><b>{html.escape(text)}</b>{body}</div>' \
        if items else f'<div class="note {"warn" if level == "warn" else ""}">{html.escape(text)}</div>'


def note(st, text, items=(), level="info"):
    st.markdown(note_html(text, items, level), unsafe_allow_html=True)


def spread(values: list[float], gap: float) -> list[float]:
    """線の右端ラベルが重ならないよう、最小間隔gapを保って上下にずらした位置を返す（順序は保つ）。"""
    order = sorted(range(len(values)), key=lambda i: values[i])
    pos = [0.0] * len(values)
    prev = None
    for i in order:
        pos[i] = values[i] if prev is None else max(values[i], prev + gap)
        prev = pos[i]
    # 上方向にずらし過ぎた分を、全体の重心に合わせて戻す
    shift = (sum(pos) - sum(values)) / len(values)
    return [p - shift for p in pos]


def base_layout(fig, height=380, right_margin=24):
    fig.update_layout(
        height=height, margin=dict(t=12, b=8, l=0, r=right_margin), showlegend=False,
        font=dict(family=FONT, color=INK, size=13), paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)", hovermode="x unified",
        hoverlabel=dict(font_family=FONT))
    fig.update_xaxes(showgrid=False, linecolor=RULE, tickfont=dict(color=MUTED))
    fig.update_yaxes(gridcolor=RULE, zeroline=False, tickfont=dict(color=MUTED))
    return fig


def line_chart(series: dict, height=380, color_for=lambda n: COLORS.get(n, MUTED)) -> go.Figure:
    """series: {名前: pandas.Series(index=日付)}。凡例は使わず、線の右端に名前と値を直接書く。"""
    fig = go.Figure()
    lasts = []
    for name, s in series.items():
        ref = name in REFERENCE
        fig.add_trace(go.Scatter(x=s.index, y=s, name=name, mode="lines",
                                 line=dict(color=color_for(name), width=1.4 if ref else 3,
                                           dash="dot" if ref else "solid"),
                                 hovertemplate="%{y:,.0f}円"))
        lasts.append((name, s.index[-1], float(s.iloc[-1])))
    if lasts:
        ys = [v for _, _, v in lasts]
        span = (max(ys) - min(ys)) or max(abs(ys[0]) * 0.02, 1.0)
        for (name, x, v), y in zip(lasts, spread(ys, span * 0.07)):
            fig.add_annotation(x=x, y=y, text=f"{html.escape(name)}　{v:,.0f}", showarrow=False,
                               xanchor="left", xshift=8, font=dict(color=color_for(name), size=12))
    base_layout(fig, height, right_margin=250)
    fig.update_yaxes(tickformat=",.0f", ticksuffix="円")
    if series:  # 右端のラベルで横軸が先まで伸びないよう、データの範囲に固定する
        fig.update_xaxes(range=[min(s.index.min() for s in series.values()),
                                max(s.index.max() for s in series.values())])
    return fig


def sector_bars(labels, values, height=480) -> go.Figure:
    fig = go.Figure(go.Bar(x=values, y=labels, orientation="h",
                           marker_color=[UP if v >= 0 else DOWN for v in values],
                           hovertemplate="%{y}: %{x:+.1%}<extra></extra>"))
    base_layout(fig, height)
    fig.update_layout(hovermode="closest")
    fig.update_xaxes(tickformat=".0%", gridcolor=RULE, showgrid=True)
    fig.update_yaxes(autorange="reversed", showgrid=False)
    return fig
