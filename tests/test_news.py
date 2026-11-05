from datetime import date

import pandas as pd

from src import data, news, prompt

ROW = """<tr>
<td class="oddnew-L kjTime" noWrap>{t}</td>
<td class="oddnew-M kjCode" noWrap>{code}</td>
<td class="oddnew-M kjName" noWrap>{name}      </td>
<td class="oddnew-M kjTitle" align="left"><a href="{href}" target="_blank">{title}</a></td>
<td class="oddnew-M kjXbrl" noWrap align="center"> </td>
</tr>"""


def page(rows, total=None):
    return "<html>全%d件<table>%s</table></html>" % (total or len(rows), "".join(ROW.format(**r) for r in rows))


R1 = dict(t="19:00", code="45230", name="エーザイ", href="a1.pdf", title="売上収益（速報）について")
R2 = dict(t="10:00", code="72030", name="トヨタ", href="a2.pdf", title="自己株式の取得に関するお知らせ")
R3 = dict(t="15:00", code="130A0", name="英数コード", href="a3.pdf", title="英数字コードの銘柄")


def test_parse_page_maps_5digit_code_to_ticker():
    df = news.parse_page(page([R1, R3]), date(2026, 10, 28))
    assert list(df["ticker"]) == ["4523", "130A"] and df["title"][0] == "売上収益（速報）について"
    assert df["url"][0] == news.BASE + "a1.pdf" and df["time"][0] == "19:00"


def test_fetch_day_reads_all_pages_and_handles_no_disclosures():
    pages = {"I_list_001_20261028.html": page([R1], total=150), "I_list_002_20261028.html": page([R2])}
    calls = []

    def get(url):
        calls.append(url)
        return pages.get(url.rsplit("/", 1)[1])
    df = news.fetch_day(date(2026, 10, 28), get, pause=0)
    assert len(df) == 2 and len(calls) == 2                        # 150件=2ページ
    assert news.fetch_day(date(2026, 11, 3), lambda u: None, pause=0).empty   # 休場日などは空


def test_save_is_append_only(tmp_path):
    con = data.connect(tmp_path / "t.db")
    df = news.parse_page(page([R1, R2]), date(2026, 10, 28))
    assert news.save(con, df) == 2 and news.save(con, df) == 0


def _db(tmp_path):
    con = data.connect(tmp_path / "t.db")
    rows = [
        ("2026-10-27", "14:00", "7203", "昼の開示（前営業日15:30より前＝すでに株価に反映）"),
        ("2026-10-27", "16:00", "7203", "前営業日の夕方"),
        ("2026-10-28", "10:00", "7203", "判断日の昼"),
        ("2026-10-28", "20:00", "4523", "判断日の夜"),
        ("2026-10-29", "08:00", "7203", "翌日の朝（判断日より後）"),
        ("2026-10-28", "12:00", "9999", "対象外の銘柄"),
    ]
    news.save(con, pd.DataFrame([(f"u{i}", d, t, k, "n", title) for i, (d, t, k, title) in enumerate(rows)],
                                columns=news.COLUMNS))
    return con


def test_for_prompt_window_excludes_future_and_old(tmp_path):
    con = _db(tmp_path)
    got = news.for_prompt(con, ["7203", "4523"], date(2026, 10, 28))
    titles = list(got["title"])
    assert "翌日の朝（判断日より後）" not in titles and "対象外の銘柄" not in titles
    assert not any("すでに株価に反映" in t for t in titles)
    assert set(titles) == {"前営業日の夕方", "判断日の昼", "判断日の夜"}
    assert list(got["ticker"])[0] == "7203"                          # 渡した順（保有銘柄が先）


def test_for_prompt_window_over_holiday_and_caps(tmp_path):
    con = data.connect(tmp_path / "t.db")
    rows = [("2026-11-02", "14:00", "古い"), ("2026-11-02", "16:00", "祝日をまたぐ前営業日の夕方")] + \
           [("2026-11-04", f"{9 + i}:00", f"当日{i}") for i in range(5)]
    news.save(con, pd.DataFrame([(f"u{i}", d, t, "7203", "n", title) for i, (d, t, title) in enumerate(rows)],
                                columns=news.COLUMNS))
    got = news.for_prompt(con, ["7203"], date(2026, 11, 4), per_ticker=10)   # 11/3は祝日なので前営業日は11/2
    assert "古い" not in set(got["title"]) and "祝日をまたぐ前営業日の夕方" in set(got["title"])
    assert len(news.for_prompt(con, ["7203"], date(2026, 11, 4))) == 3         # 1銘柄3件まで

def test_prompt_includes_news_and_records_used(tmp_path):
    con = _db(tmp_path)
    used = news.for_prompt(con, ["7203"], date(2026, 10, 28))
    from src import market
    idx = pd.DataFrame(columns=["指標", "値", "本日", "五日", "二十日"])
    text = prompt.build_prompt(model="gpt", as_of="2026-10-28", initial=300000, equity=300000, cash=300000,
                               positions={}, index_df=idx, breadth=None, sectors=pd.DataFrame(columns=["業種", "銘柄数", "本日", "五日", "二十日"]),
                               cands=pd.DataFrame(columns=["ticker", "close", "ret1", "ret5", "ret20", "gap20", "gap60", "vol_ratio", "dist_high60", "reason"]),
                               news=used)
    assert "判断日の昼" in text and "翌日の朝" not in text
    news.record_used(con, "gpt", "2026-10-28", used["url"])
    news.record_used(con, "gpt", "2026-10-28", used["url"])         # 二重登録しない
    assert con.execute("SELECT COUNT(*) FROM prompt_news").fetchone()[0] == len(used)


def test_prompt_without_news_says_none():
    idx = pd.DataFrame(columns=["指標", "値", "本日", "五日", "二十日"])
    text = prompt.build_prompt(model="gpt", as_of="2026-10-28", initial=300000, equity=1, cash=1, positions={},
                               index_df=idx, breadth=None, sectors=pd.DataFrame(columns=["業種", "銘柄数", "本日", "五日", "二十日"]),
                               cands=pd.DataFrame(columns=["ticker", "close", "ret1", "ret5", "ret20", "gap20", "gap60", "vol_ratio", "dist_high60", "reason"]))
    assert "該当する開示なし" in text



def test_used_for_joins_recorded_news_by_model_and_day(tmp_path):
    con = _db(tmp_path)
    urls = news.for_prompt(con, ["7203"], date(2026, 10, 28))["url"]
    news.record_used(con, "gpt", "2026-10-28", urls)
    got = news.used_for(con, "gpt", "2026-10-28")
    assert set(got["url"]) == set(urls) and set(got["ticker"]) == {"7203"}
    assert news.used_for(con, "claude", "2026-10-28").empty          # 記録のないモデル・日は空


def test_for_prompt_cuts_off_at_run_time(tmp_path):
    con = data.connect(tmp_path / "t.db")
    rows = [("2026-10-28", "21:30", "締め切りちょうど"), ("2026-10-28", "21:31", "締め切りの後"),
            ("2026-10-28", "16:00", "夕方")]
    news.save(con, pd.DataFrame([(f"u{i}", d, t, "7203", "n", title) for i, (d, t, title) in enumerate(rows)],
                                columns=news.COLUMNS))
    got = set(news.for_prompt(con, ["7203"], date(2026, 10, 28))["title"])
    assert got == {"締め切りちょうど", "夕方"}                       # いつ依頼文を作っても同じ内容
