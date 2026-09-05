"""JPX 投資部門別売買状況 ビューア

データは GitHub 上の CSV（scripts/fetch_jpx.py が生成）を読み込む。
取得できない場合は同梱の data/investor_weekly.csv にフォールバックする。
表示は金額ベースのみ。週次・月次・四半期の3通りに集計できる。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ---------------------------------------------------------------- 設定

# ここを自分のリポジトリに書き換える（.streamlit/secrets.toml の data_url が優先）
DEFAULT_DATA_URL = (
    "https://raw.githubusercontent.com/USERNAME/jpx-investor-app/main/data/investor_weekly.csv"
)
LOCAL_DATA = Path(__file__).parent / "data" / "investor_weekly.csv"

# 投資部門の内訳（総計・委託計・法人・金融機関 は合計項目なので割合の計算から外す）
LEAF_INVESTORS = [
    "自己計", "海外投資家", "個人", "投資信託", "事業法人", "その他法人等",
    "信託銀行", "生保・損保", "都銀・地銀等", "その他金融機関", "証券会社",
]

C_BUY = "#ED7D31"   # 買い
C_SELL = "#4472C4"  # 売り
C_NET = "#A5A5A5"   # 差引

# 集計単位ごとの設定（表示名, 期間の選択肢, ラベルの呼び方, x軸の間隔の目安）
FREQS = {
    "週次": {"code": "W", "spans": {"26週": 26, "52週": 52, "全期間": None},
             "default": 52, "noun": "週", "gap": 7},
    "月次": {"code": "M", "spans": {"12ヶ月": 12, "24ヶ月": 24, "全期間": None},
             "default": 12, "noun": "月", "gap": 30},
    "四半期": {"code": "Q", "spans": {"8四半期": 8, "全期間": None},
              "default": 8, "noun": "四半期", "gap": 91},
}

st.set_page_config(
    page_title="JPX 投資部門別売買状況",
    page_icon="📈",
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
      .block-container {padding: 1rem 0.8rem 3rem;}
      @media (max-width: 640px) {
        .block-container {padding-left: 0.4rem; padding-right: 0.4rem;}
        h1 {font-size: 1.3rem !important;}
      }
      [data-testid="stMetricValue"] {font-size: 1.1rem;}
      [data-testid="stMetricLabel"] {font-size: 0.75rem; opacity: 0.75;}
      div[data-testid="stElementToolbar"] {display: none;}
    </style>
    """,
    unsafe_allow_html=True,
)


def theme() -> dict:
    """ライト／ダークどちらでも読める色を返す。"""
    try:
        base = st.context.theme.type or "light"
    except Exception:
        base = st.get_option("theme.base") or "light"
    if base == "dark":
        return {"text": "#E6E6E6", "grid": "rgba(255,255,255,0.16)",
                "zero": "rgba(255,255,255,0.55)", "tick": "rgba(255,255,255,0.45)",
                "hover": "rgba(30,30,30,0.92)", "label": "#FFFFFF"}
    return {"text": "#31333F", "grid": "rgba(0,0,0,0.10)",
            "zero": "rgba(0,0,0,0.45)", "tick": "rgba(255,255,255,0.75)",
            "hover": "rgba(255,255,255,0.95)", "label": "#000000"}


def axis_scale(peak: float) -> tuple[float, str]:
    """軸が 400k のような表示にならないよう、大きい値は兆円に繰り上げる。"""
    return (10000.0, "兆円") if peak >= 10000 else (1.0, "億円")


# ---------------------------------------------------------------- データ


def data_url() -> str:
    try:
        return st.secrets["data_url"]
    except Exception:
        return DEFAULT_DATA_URL


@st.cache_data(ttl=3600, show_spinner=False)
def load_data(url: str) -> tuple[pd.DataFrame, str]:
    """GitHub の CSV を読み、失敗したらローカルの CSV を使う。"""
    df, source = None, ""
    if url and "USERNAME" not in url:
        try:
            df = pd.read_csv(url)
            source = "GitHub"
        except Exception:
            df = None
    if df is None and LOCAL_DATA.exists():
        df = pd.read_csv(LOCAL_DATA)
        source = "同梱ファイル"
    if df is None:
        raise FileNotFoundError("データが見つかりません。scripts/fetch_jpx.py を実行してください。")

    df["week_end"] = pd.to_datetime(df["week_end"])
    for c in ("sales", "purchases", "balance"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    if "basis" in df:
        df = df[df["basis"] == "金額"]  # 表示は金額ベースのみ
    # 百万円 → 億円
    for c in ("sales", "purchases", "balance"):
        df[c] = df[c] / 100
    return df.sort_values("week_end"), source


def aggregate(sel: pd.DataFrame, code: str) -> pd.DataFrame:
    """週次のデータを月次・四半期にまとめる。x, tick, label 列を付けて返す。"""
    if code == "W":
        out = sel.copy().reset_index(drop=True)
        out["x"] = out["week_end"]
        out["tick"] = out["week_end"].dt.strftime("%d")
        out["label"] = out.get("week_label", out["week_end"].dt.strftime("%Y-%m-%d"))
        return out

    per = sel["week_end"].dt.to_period("M" if code == "M" else "Q").rename("period")
    out = (
        sel.groupby(per)
        .agg(sales=("sales", "sum"),
             purchases=("purchases", "sum"),
             balance=("balance", "sum"),
             weeks=("balance", "size"))
        .reset_index()
    )
    out["x"] = out["period"].dt.to_timestamp(how="end").dt.normalize()
    if code == "M":
        out["tick"] = out["period"].apply(lambda p: str(p.month))
        out["label"] = out["period"].apply(lambda p: f"{p.year}年{p.month}月")
    else:
        out["tick"] = out["period"].apply(lambda p: f"{p.quarter}Q")
        out["label"] = out["period"].apply(lambda p: f"{p.year}年{p.quarter}Q")
    return out


# ---------------------------------------------------------------- グラフ


def period_marks(d: pd.DataFrame, code: str, step: int, col: dict) -> tuple[list[dict], list[dict]]:
    """ゼロ線の下に、日付・月・四半期・年を段組みで置く。

    上から順に「日付（週次なら週末日、月次なら月、四半期なら1Q〜4Q）」「月」「四半期」「年」。
    週次以外は月・四半期の段を持たない。区切り線は週次が月替わり、それ以外は年替わり。
    """
    shapes: list[dict] = []
    labels: list[dict] = []

    x = pd.to_datetime(d["x"]).reset_index(drop=True)
    g = pd.DataFrame({"x": x, "tick": d["tick"].reset_index(drop=True)})
    g["ym"] = x.dt.to_period("M")
    g["yq"] = x.dt.to_period("Q")
    g["y"] = x.dt.year

    rows = {"tick": 0.46, "month": 0.34, "quarter": 0.22, "year": 0.10}
    if code != "W":
        rows["year"] = 0.32

    boundary = "ym" if code == "W" else "y"
    line_bottom = 0.05 if code == "W" else 0.27
    for i in range(1, len(g)):
        if g.loc[i, boundary] != g.loc[i - 1, boundary]:
            strong = code != "W" or g.loc[i, "yq"] != g.loc[i - 1, "yq"]
            border = g.loc[i - 1, "x"] + (g.loc[i, "x"] - g.loc[i - 1, "x"]) / 2
            shapes.append(
                dict(type="line", xref="x", yref="paper", x0=border, x1=border,
                     y0=line_bottom, y1=0.53 if strong else 0.41,
                     line=dict(color=col["tick"], width=1), layer="above")
            )

    def label(pos, y: float, text: str) -> None:
        labels.append(
            dict(x=pos, y=y, xref="x", yref="paper", text=text, showarrow=False,
                 font=dict(size=10 if y == rows["tick"] else 11, color=col["label"]),
                 opacity=0.95)
        )

    # 日付（間引きあり）
    for _, r in g.iloc[::step].iterrows():
        label(r["x"], rows["tick"], str(r["tick"]))

    lo, hi = g["x"].min(), g["x"].max()
    span = hi - lo

    def add(group_col: str, text_fn, y: float) -> None:
        for key, sub in g.groupby(group_col, sort=True):
            pos = sub["x"].mean()
            if span.days:  # 端のラベルが切れないよう内側へ寄せる
                pos = min(max(pos, lo + span * 0.07), hi - span * 0.07)
            label(pos, y, text_fn(key))

    if code == "W":
        add("ym", lambda k: str(k.month), rows["month"])
        if g["yq"].nunique() > 1:
            add("yq", lambda k: f"{k.quarter}Q", rows["quarter"])
    if g["y"].nunique() > 1:
        add("y", lambda k: str(k), rows["year"])
    return shapes, labels


def net_scale_for(balance: pd.Series, gross_max: float) -> int:
    """差引は売買代金より1〜2桁小さいので、面が潰れない倍率を自動で選ぶ。"""
    peak = float(balance.abs().max())
    if peak <= 0 or gross_max <= 0:
        return 1
    target = gross_max * 0.4 / peak
    return min((1, 2, 5, 10, 20, 50, 100), key=lambda s: abs(s - target))


def flow_chart(d: pd.DataFrame, code: str, div: float, axis_unit: str,
               net_scale: int, height: int) -> go.Figure:
    """買い（上）・売り（下）・差引（灰・拡大表示）の面グラフ。"""
    col = theme()
    x = pd.to_datetime(d["x"])
    n = len(d)
    if code == "W":
        step = 1 if n <= 14 else (2 if n <= 30 else 4)
    else:
        step = 1 if n <= 26 else 2

    def trace(y, hover, name, color, tmpl):
        return go.Scatter(
            x=x, y=y / div, name=name, mode="lines",
            line=dict(color=color, width=1), fill="tozeroy", fillcolor=color,
            customdata=hover, hovertemplate=tmpl,
        )

    fig = go.Figure()
    fig.add_trace(trace(d["purchases"], d["purchases"], "買い", C_BUY,
                        "買い %{customdata:,.0f}<extra></extra>"))
    fig.add_trace(trace(-d["sales"], d["sales"], "売り", C_SELL,
                        "売り %{customdata:,.0f}<extra></extra>"))
    fig.add_trace(trace(d["balance"] * net_scale, d["balance"],
                        f"差引（{net_scale}倍）" if net_scale > 1 else "差引", C_NET,
                        "差引 %{customdata:,.0f}<extra></extra>"))

    ymax = float(max(d["purchases"].max(), d["sales"].max(),
                     (d["balance"].abs() * net_scale).max())) / div * 1.12 or 1.0

    shapes, labels = period_marks(d, code, step, col)
    shapes.append(dict(type="line", xref="paper", yref="y", x0=0, x1=1, y0=0, y1=0,
                       line=dict(color=col["zero"], width=1)))

    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=8, b=40),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=col["text"], size=11),
        hovermode="x unified",
        hoverlabel=dict(bgcolor=col["hover"], font_size=12),
        legend=dict(orientation="h", yanchor="top", y=-0.04, xanchor="center", x=0.5),
        shapes=shapes,
        annotations=labels,
        dragmode=False,
    )
    half = pd.Timedelta(days=FREQS_BY_CODE[code]["gap"] / 2)
    fig.update_xaxes(
        showticklabels=False,  # 日付はゼロ線の下に注釈として描く
        showgrid=False, zeroline=False, showline=False,
        range=[x.min() - half, x.max() + half],
        hoverformat="%Y/%m/%d",
        fixedrange=True,
    )
    fig.update_yaxes(
        title=None, range=[-ymax, ymax], gridcolor=col["grid"], zeroline=False,
        tickfont=dict(size=10, color=col["text"]),
        ticksuffix=f" {axis_unit}", tickformat=",.4~g", fixedrange=True, nticks=10,
    )
    return fig


def cumulative_chart(d: pd.DataFrame, div: float, axis_unit: str, height: int) -> go.Figure:
    """差引の累積。買い越し／売り越しの積み上がりを見る。"""
    col = theme()
    cum = d["balance"].cumsum()
    fig = go.Figure(
        go.Scatter(
            x=pd.to_datetime(d["x"]), y=cum / div, mode="lines",
            line=dict(color=C_BUY, width=2),
            fill="tozeroy", fillcolor="rgba(237,125,49,0.22)",
            customdata=cum, hovertemplate="累積 %{customdata:,.0f}<extra></extra>",
        )
    )
    fig.update_layout(
        height=height, margin=dict(l=8, r=8, t=8, b=8),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=col["text"], size=11), hovermode="x unified",
        hoverlabel=dict(bgcolor=col["hover"], font_size=12), dragmode=False,
    )
    fig.update_xaxes(showgrid=False, fixedrange=True, tickfont=dict(size=10),
                     tickformat="%y/%m", hoverformat="%Y/%m/%d", nticks=6)
    fig.update_yaxes(gridcolor=col["grid"], zerolinecolor=col["zero"],
                     ticksuffix=f" {axis_unit}", tickformat=",.4~g",
                     fixedrange=True, tickfont=dict(size=10))
    return fig


def investor_stats(dfm: pd.DataFrame, code: str, x_min, x_max) -> pd.DataFrame:
    """市場全体を投資部門ごとに集計し、割合と差引の傾向を並べる。"""
    parts: dict[str, pd.DataFrame] = {}
    for inv, sub in dfm.groupby("investor"):
        a = aggregate(sub.sort_values("week_end"), code)
        a = a[(a["x"] >= x_min) & (a["x"] <= x_max)]
        if not a.empty:
            parts[inv] = a

    leaves = [i for i in LEAF_INVESTORS if i in parts]
    if not leaves:
        return pd.DataFrame()

    if "総計" in parts:
        total = float(parts["総計"]["sales"].sum() + parts["総計"]["purchases"].sum())
    else:
        total = float(sum(parts[i]["sales"].sum() + parts[i]["purchases"].sum() for i in leaves))

    rows = []
    for inv in leaves:
        a = parts[inv]
        b = a["balance"]
        gross = float(a["sales"].sum() + a["purchases"].sum())
        abs_sum = float(b.abs().sum())
        rows.append({
            "部門": inv,
            "売買シェア": gross / total * 100 if total else 0.0,
            "累計差引": float(b.sum()),
            "平均差引": float(b.mean()),
            "ブレ": float(b.std(ddof=0)),
            "一貫性": abs(float(b.sum())) / abs_sum if abs_sum else 0.0,
        })
    out = pd.DataFrame(rows)
    return out.reindex(out["累計差引"].abs().sort_values(ascending=False).index).reset_index(drop=True)


def rank_chart(stats: pd.DataFrame, column: str, unit: str, highlight: str,
               diverging: bool) -> go.Figure:
    """部門別の横棒グラフ。diverging=True なら買い越し／売り越しで色を分ける。"""
    col = theme()
    d = stats.sort_values(column, ascending=True)
    if diverging:
        colors = [C_BUY if v >= 0 else C_SELL for v in d[column]]
    else:
        colors = [C_NET if n != highlight else C_BUY for n in d["部門"]]
    line = ["#000000" if n == highlight else "rgba(0,0,0,0)" for n in d["部門"]]

    fig = go.Figure(
        go.Bar(
            x=d[column], y=d["部門"], orientation="h",
            marker=dict(color=colors, line=dict(color=line, width=1.5)),
            text=[f"{v:,.1f}" if unit == "%" else f"{v:,.0f}" for v in d[column]],
            textposition="auto", textfont=dict(size=10),
            hovertemplate="%{y} %{x:,.1f}" + unit + "<extra></extra>",
        )
    )
    fig.update_layout(
        height=max(200, 26 * len(d) + 40),
        margin=dict(l=8, r=8, t=8, b=8),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=col["text"], size=11),
        showlegend=False, dragmode=False, bargap=0.25,
    )
    fig.update_xaxes(gridcolor=col["grid"], zerolinecolor=col["zero"],
                     ticksuffix=f" {unit}" if unit != "%" else "%",
                     fixedrange=True, tickfont=dict(size=10))
    fig.update_yaxes(showgrid=False, fixedrange=True, tickfont=dict(size=11, color=col["text"]))
    return fig


FREQS_BY_CODE = {v["code"]: v for v in FREQS.values()}

# ---------------------------------------------------------------- 画面

st.title("JPX 投資部門別売買状況")

try:
    df, source = load_data(data_url())
except Exception as e:
    st.error(str(e))
    st.stop()

markets = sorted(df["market"].unique())
investors = list(dict.fromkeys(df["investor"]))

col1, col2 = st.columns(2)
with col1:
    market = st.selectbox("市場", markets,
                          index=markets.index("東証プライム") if "東証プライム" in markets else 0)
with col2:
    if "investor" not in st.session_state:
        st.session_state["investor"] = "海外投資家" if "海外投資家" in investors else investors[0]
    investor = st.selectbox("投資部門", investors, key="investor")

freq_name = st.radio("集計", list(FREQS), horizontal=True, label_visibility="collapsed")
freq = FREQS[freq_name]

sel = df[(df["market"] == market) & (df["investor"] == investor)].copy()
if sel.empty:
    st.warning("該当するデータがありません。")
    st.stop()

full = aggregate(sel, freq["code"])
labels = full["label"].tolist()

# 期間はスライダーで自由に選ぶ。ボタンはその初期位置を決めるショートカット。
range_key = f"range_{freq['code']}"
default_n = min(freq["default"], len(labels))
if range_key not in st.session_state or not set(st.session_state[range_key]) <= set(labels):
    st.session_state[range_key] = (labels[-default_n], labels[-1])

btns = st.columns(len(freq["spans"]))
for (name, count), box in zip(freq["spans"].items(), btns):
    if box.button(name, width="stretch"):
        st.session_state[range_key] = (labels[-min(count or len(labels), len(labels))], labels[-1])
        st.rerun()

start_label, end_label = st.select_slider(
    "期間", options=labels, value=st.session_state[range_key],
    key=range_key, label_visibility="collapsed",
)
i0, i1 = labels.index(start_label), labels.index(end_label)
view = full.iloc[i0 : i1 + 1].reset_index(drop=True)
if len(view) < 2:
    st.warning("期間が短すぎます。スライダーの幅を広げてください。")
    st.stop()

latest = view.iloc[-1]
noun = freq["noun"]
m1, m2, m3 = st.columns(3)
m1.metric(f"最終{noun} 差引（億円）", f"{latest['balance']:,.0f}")
m2.metric(f"期間合計（億円）", f"{view['balance'].sum():,.0f}")
ytd = full[pd.to_datetime(full["x"]).dt.year == pd.Timestamp(latest["x"]).year]["balance"].sum()
m3.metric("年初来（億円）", f"{ytd:,.0f}")

st.caption(f"{market} ／ {investor} ／ {freq_name}　{view.iloc[0]['label']} 〜 {view.iloc[-1]['label']}（{len(view)}{noun}）")

gross_peak = float(max(view["purchases"].max(), view["sales"].max()))
net_scale = net_scale_for(view["balance"], gross_peak)
div, axis_unit = axis_scale(gross_peak)

tab1, tab2, tab3, tab4 = st.tabs([freq_name, "累積差引", "部門別", "データ"])
plot_config = {"displayModeBar": False, "scrollZoom": False,
               "doubleClick": False, "responsive": True}

with tab1:
    st.plotly_chart(
        flow_chart(view, freq["code"], div, axis_unit, net_scale, height=420),
        width="stretch", config=plot_config,
    )
    scale_note = f"（見やすさのため {net_scale} 倍表示）" if net_scale > 1 else ""
    axis_note = {"W": "横軸の数字は週末日、区切り線は月の変わり目。",
                 "M": "横軸の数字は月、区切り線は年の変わり目。",
                 "Q": "横軸は四半期、区切り線は年の変わり目。"}[freq["code"]]
    st.caption(
        f"買い＝上向き、売り＝下向き、灰色＝差引{scale_note}。{axis_note}"
        f"縦軸の単位は{axis_unit}、タップして出る数値は億円。"
    )

with tab2:
    cum_div, cum_unit = axis_scale(float(full["balance"].cumsum().abs().max()))
    st.plotly_chart(cumulative_chart(full, cum_div, cum_unit, height=340),
                    width="stretch", config=plot_config)
    st.caption("全期間の差引累計。上向きなら買い越しが積み上がっている状態。")

with tab3:
    stats = investor_stats(df[df["market"] == market], freq["code"],
                           pd.Timestamp(view["x"].min()), pd.Timestamp(view["x"].max()))
    if stats.empty:
        st.info("集計できる投資部門がありません。")
    else:
        st.markdown("**売買シェア** — 売買代金に占める割合。市場の主役はどこか。")
        st.plotly_chart(rank_chart(stats, "売買シェア", "%", investor, diverging=False),
                        width="stretch", config=plot_config)

        st.markdown(f"**累計差引（億円）** — この期間にどれだけ買い越し／売り越したか。")
        st.plotly_chart(rank_chart(stats, "累計差引", "億円", investor, diverging=True),
                        width="stretch", config=plot_config)

        show = stats.copy()
        show["売買シェア"] = show["売買シェア"].round(1)
        for c in ("累計差引", "平均差引", "ブレ"):
            show[c] = show[c].round(0)
        show["一貫性"] = show["一貫性"].round(2)
        show = show.rename(columns={"売買シェア": "シェア(%)", "累計差引": f"累計差引(億円)",
                                    "平均差引": f"1{noun}平均(億円)", "ブレ": "ブレ(億円)"})
        try:
            event = st.dataframe(show, width="stretch", hide_index=True, height=330,
                                 on_select="rerun", selection_mode="single-row")
            picked = event.selection["rows"]
        except TypeError:  # 古い Streamlit では行選択が使えない
            st.dataframe(show, width="stretch", hide_index=True, height=330)
            picked = []

        if picked:
            name = stats.iloc[picked[0]]["部門"]
            if name != investor and st.button(f"「{name}」の推移を見る", width="stretch"):
                st.session_state["investor"] = name
                st.rerun()

        st.caption(
            "シェアが大きいほど売買の主役。一貫性（0〜1）は差引が一方向に偏っている度合いで、"
            "1に近いほど毎回同じ向きに積み上げている。ブレは1期あたりの差引の標準偏差で、"
            "大きいほど週ごとの振れが市場を動かしやすい。"
            "シェアが小さくても一貫性が高い部門（信託銀行・事業法人など）は効きやすい。"
        )

with tab4:
    cols = ["label", "sales", "purchases", "balance"]
    names = {"label": "期間", "sales": "売り(億円)",
             "purchases": "買い(億円)", "balance": "差引(億円)"}
    if "weeks" in view:
        cols.append("weeks")
        names["weeks"] = "週数"
    table = view[cols].rename(columns=names)
    st.dataframe(table.iloc[::-1], width="stretch", hide_index=True, height=380)
    st.download_button(
        "CSVをダウンロード",
        table.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"jpx_{market}_{investor}_{freq_name}.csv",
        mime="text/csv", width="stretch",
    )

st.caption(
    f"データ出所：日本取引所グループ「投資部門別売買状況」（読み込み元：{source}）　"
    "※金額ベース。元データは百万円で保持し、億円に換算して表示しています。"
)
