import pandas as pd

from src import ui


def test_spread_keeps_order_and_min_gap():
    pos = ui.spread([300000.0, 300000.0, 300000.0, 310000.0], gap=1000)
    order = sorted(range(4), key=lambda i: pos[i])
    ordered = [pos[i] for i in order]
    assert all(b - a >= 1000 - 1e-9 for a, b in zip(ordered, ordered[1:]))
    assert pos[3] == max(pos)                               # 最上位は最上位のまま
    assert abs(sum(pos) / 4 - sum([300000, 300000, 300000, 310000]) / 4) < 1e-6   # 重心は保つ


def test_spread_leaves_distant_values_alone():
    assert ui.spread([100.0, 500.0], gap=10) == [100.0, 500.0]


def test_strip_escapes_html_and_marks_abnormal_only():
    h = ui.strip_html([
        {"label": "鮮度", "value": "最新", "sub": "2026-10-28", "level": "ok"},
        {"label": "取得", "value": "<script>x</script>", "sub": "", "level": "bad"}])
    assert "<script>" not in h and "&lt;script&gt;" in h
    assert h.count('class="g"') == 1            # 記号が付くのは異常側だけ（正常は何も付けない）
    assert "■" in h and "●" not in h


def test_rows_pending_is_emphasized():
    h = ui.rows_html([{"name": "gpt", "state": "判断待ち", "level": "warn", "detail": "未登録"},
                      {"name": "claude", "state": "登録済み", "level": "ok", "detail": "13件"}])
    assert 'state warn' in h and 'state ok' in h and h.count("▲") == 1


def test_line_chart_labels_every_series_without_legend():
    idx = pd.date_range("2026-10-28", periods=3)
    s = {"gpt": pd.Series([300000.0] * 3, index=idx), "claude": pd.Series([300000.0] * 3, index=idx),
         "日経平均": pd.Series([300000.0, 301000.0, 302000.0], index=idx)}
    fig = ui.line_chart(s)
    assert not fig.layout.showlegend and len(fig.layout.annotations) == 3
    ys = [a.y for a in fig.layout.annotations]
    assert len(set(round(y, 3) for y in ys)) == 3          # 同値でもラベルが重ならない
    dotted = {t.name: t.line.dash for t in fig.data}
    assert dotted["日経平均"] == "dot" and dotted["gpt"] == "solid"


def test_market_colors_follow_japanese_convention():
    fig = ui.sector_bars(["a", "b"], [0.05, -0.05])
    assert list(fig.data[0].marker.color) == [ui.UP, ui.DOWN]
