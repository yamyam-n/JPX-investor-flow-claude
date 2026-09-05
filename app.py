"""JPX 投資部門別売買状況 ビューア（週間）

データは GitHub 上の CSV（scripts/fetch_jpx.py が生成）を読み込む。
取得できない場合は同梱の data/investor_weekly.csv にフォールバックする。
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

C_BUY = "#ED7D31"   # 買い
C_SELL = "#4472C4"  # 売り
C_NET = "#A5A5A5"   # 差引
C_GRID = "rgba(255,255,255,0.16)"
C_TEXT = "#E6E6E6"

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
    return df.sort_values("week_end"), source


UNITS = {
    # basis: (表示単位, 元データからの除数)  金額=百万円 / 株数=千株
    "金額": ("億円", 100),
    "株数": ("百万株", 1000),
}

# ---------------------------------------------------------------- グラフ


def month_marks(dates: pd.Series) -> tuple[list[dict], list[dict]]:
    """月の区切り線と、月・四半期・年のラベルを作る。"""
    shapes: list[dict] = []
    labels: list[dict] = []

    d = pd.DataFrame({"date": pd.to_datetime(dates).reset_index(drop=True)})
    d["ym"] = d["date"].dt.to_period("M")
    d["yq"] = d["date"].dt.to_period("Q")
    d["y"] = d["date"].dt.year

    for i in range(1, len(d)):
        if d.loc[i, "ym"] != d.loc[i - 1, "ym"]:
            is_q = d.loc[i, "yq"] != d.loc[i - 1, "yq"]
            border = d.loc[i - 1, "date"] + (d.loc[i, "date"] - d.loc[i - 1, "date"]) / 2
            shapes.append(
                dict(
                    type="line",
                    xref="x",
                    yref="paper",
                    x0=border,
                    x1=border,
                    y0=0.03,
                    y1=0.62 if is_q else 0.45,
                    line=dict(color="rgba(255,255,255,0.45)", width=1),
                    layer="above",
                )
            )

    def add(group_col: str, text_fn, y: float, size: int) -> None:
        for key, g in d.groupby(group_col, sort=True):
            labels.append(
                dict(
                    x=g["date"].mean(),
                    y=y,
                    xref="x",
                    yref="paper",
                    text=text_fn(key),
                    showarrow=False,
                    font=dict(size=size, color=C_TEXT),
                    opacity=0.9,
                )
            )

    add("ym", lambda k: str(k.month), 0.36, 11)
    if d["yq"].nunique() > 1:
        add("yq", lambda k: f"{k.quarter}Q", 0.18, 11)
    if d["y"].nunique() > 1:
        add("y", lambda k: str(k), 0.04, 11)
    return shapes, labels


def net_scale_for(balance: pd.Series, gross_max: float) -> int:
    """差引が売買代金に対して小さいので、見やすい倍率を自動で選ぶ。"""
    peak = float(balance.abs().max())
    if peak <= 0 or gross_max <= 0:
        return 1
    target = gross_max * 0.65 / peak
    for s in (1, 2, 5, 10, 20, 50, 100):
        if s >= target:
            return s
    return 100


def flow_chart(d: pd.DataFrame, unit: str, net_scale: int, height: int) -> go.Figure:
    """買い（上）・売り（下）・差引（灰・拡大表示）の面グラフ。"""
    x = d["week_end"]
    n = len(d)
    step = 1 if n <= 14 else (2 if n <= 30 else 4)

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=x,
            y=d["purchases"],
            name="買い",
            mode="lines",
            line=dict(color=C_BUY, width=1),
            fill="tozeroy",
            fillcolor=C_BUY,
            hovertemplate="買い %{y:,.0f}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=x,
            y=-d["sales"],
            name="売り",
            mode="lines",
            line=dict(color=C_SELL, width=1),
            fill="tozeroy",
            fillcolor=C_SELL,
            customdata=d["sales"],
            hovertemplate="売り %{customdata:,.0f}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=x,
            y=d["balance"] * net_scale,
            name=f"差引（{net_scale}倍）" if net_scale > 1 else "差引",
            mode="lines",
            line=dict(color=C_NET, width=1),
            fill="tozeroy",
            fillcolor=C_NET,
            customdata=d["balance"],
            hovertemplate="差引 %{customdata:,.0f}<extra></extra>",
        )
    )

    ymax = float(
        max(d["purchases"].max(), d["sales"].max(), (d["balance"].abs() * net_scale).max())
    ) * 1.12
    ymax = ymax or 1.0

    shapes, labels = month_marks(d["week_end"])
    shapes.append(
        dict(
            type="line",
            xref="paper",
            yref="y",
            x0=0,
            x1=1,
            y0=0,
            y1=0,
            line=dict(color="rgba(255,255,255,0.55)", width=1),
        )
    )

    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=8, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=C_TEXT, size=11),
        hovermode="x unified",
        hoverlabel=dict(bgcolor="rgba(30,30,30,0.92)", font_size=12),
        legend=dict(orientation="h", yanchor="top", y=-0.02, xanchor="center", x=0.5),
        shapes=shapes,
        annotations=labels,
        dragmode=False,
    )
    half = pd.Timedelta(days=3.5)
    fig.update_xaxes(
        tickmode="array",
        tickvals=x.tolist()[::step],
        ticktext=x.dt.strftime("%d").tolist()[::step],
        tickfont=dict(size=10),
        showgrid=False,
        zeroline=False,
        showline=False,
        range=[x.min() - half, x.max() + half],
        hoverformat="%Y/%m/%d",
        fixedrange=True,
    )
    fig.update_yaxes(
        title=None,
        range=[-ymax, ymax],
        gridcolor=C_GRID,
        zeroline=False,
        tickfont=dict(size=10),
        ticksuffix=f" {unit}",
        fixedrange=True,
    )
    return fig


def cumulative_chart(d: pd.DataFrame, unit: str, height: int) -> go.Figure:
    """差引の累積。買い越し／売り越しの積み上がりを見る。"""
    fig = go.Figure(
        go.Scatter(
            x=d["week_end"],
            y=d["balance"].cumsum(),
            mode="lines",
            line=dict(color=C_BUY, width=2),
            fill="tozeroy",
            fillcolor="rgba(237,125,49,0.22)",
            hovertemplate="累積 %{y:,.0f}<extra></extra>",
        )
    )
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=8, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=C_TEXT, size=11),
        hovermode="x unified",
        dragmode=False,
    )
    fig.update_xaxes(
        showgrid=False,
        fixedrange=True,
        tickfont=dict(size=10),
        tickformat="%y/%m",
        hoverformat="%Y/%m/%d",
        nticks=6,
    )
    fig.update_yaxes(
        gridcolor=C_GRID,
        zerolinecolor="rgba(255,255,255,0.5)",
        ticksuffix=f" {unit}",
        fixedrange=True,
        tickfont=dict(size=10),
    )
    return fig


# ---------------------------------------------------------------- 画面

st.title("JPX 投資部門別売買状況")

try:
    df, source = load_data(data_url())
except Exception as e:  # データが無い / 壊れている
    st.error(str(e))
    st.stop()

markets = sorted(df["market"].unique())
investors = list(dict.fromkeys(df["investor"]))

col1, col2 = st.columns(2)
with col1:
    market = st.selectbox(
        "市場",
        markets,
        index=markets.index("東証プライム") if "東証プライム" in markets else 0,
    )
with col2:
    investor = st.selectbox(
        "投資部門",
        investors,
        index=investors.index("海外投資家") if "海外投資家" in investors else 0,
    )

basis_opts = [b for b in ("金額", "株数") if b in set(df["basis"])]
c1, c2 = st.columns([1, 2])
with c1:
    basis = st.radio("基準", basis_opts, horizontal=True, label_visibility="collapsed")
with c2:
    span = st.radio(
        "期間",
        ["26週", "52週", "全期間"],
        horizontal=True,
        label_visibility="collapsed",
    )

unit, div = UNITS[basis]

sel = df[(df["market"] == market) & (df["investor"] == investor) & (df["basis"] == basis)].copy()
if sel.empty:
    st.warning("該当するデータがありません。")
    st.stop()

for c in ("sales", "purchases", "balance"):
    sel[c] = sel[c] / div

n = {"26週": 26, "52週": 52, "全期間": len(sel)}[span]
view = sel.tail(n).reset_index(drop=True)

latest = view.iloc[-1]
m1, m2, m3 = st.columns(3)
m1.metric(f"最新週 差引（{unit}）", f"{latest['balance']:,.0f}")
m2.metric(f"直近4週（{unit}）", f"{view['balance'].tail(4).sum():,.0f}")
ytd = sel[sel["week_end"].dt.year == latest["week_end"].year]["balance"].sum()
m3.metric(f"年初来（{unit}）", f"{ytd:,.0f}")

st.caption(
    f"{market} ／ {investor} ／ {basis}　最新週：{latest['week_end']:%Y-%m-%d}"
    f"（{latest.get('week_label', '')}）"
)

net_scale = net_scale_for(view["balance"], float(max(view["purchases"].max(), view["sales"].max())))
tab1, tab2, tab3 = st.tabs(["週次", "累積差引", "データ"])

plot_config = {
    "displayModeBar": False,
    "scrollZoom": False,
    "doubleClick": False,
    "responsive": True,
}

with tab1:
    st.plotly_chart(
        flow_chart(view, unit, net_scale, height=420),
        width="stretch",
        config=plot_config,
    )
    scale_note = f"（見やすさのため {net_scale} 倍表示）" if net_scale > 1 else ""
    st.caption(
        f"買い＝上向き、売り＝下向き、灰色＝差引{scale_note}。"
        f"横軸の数字は週末日、区切り線は月の変わり目。単位：{unit}"
    )

with tab2:
    st.plotly_chart(
        cumulative_chart(sel, unit, height=340),
        width="stretch",
        config=plot_config,
    )
    st.caption("全期間の差引累計。上向きなら買い越しが積み上がっている状態。")

with tab3:
    table = view[["week_end", "week_label", "sales", "purchases", "balance"]].copy()
    table["week_end"] = table["week_end"].dt.strftime("%Y-%m-%d")
    table = table.rename(
        columns={
            "week_end": "週末",
            "week_label": "週",
            "sales": f"売り({unit})",
            "purchases": f"買い({unit})",
            "balance": f"差引({unit})",
        }
    )
    st.dataframe(
        table.iloc[::-1],
        width="stretch",
        hide_index=True,
        height=380,
    )
    st.download_button(
        "CSVをダウンロード",
        table.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"jpx_{market}_{investor}_{basis}.csv",
        mime="text/csv",
        width="stretch",
    )

st.caption(
    f"データ出所：日本取引所グループ「投資部門別売買状況」（読み込み元：{source}）　"
    "※金額は百万円、株数は千株で保持し、表示時に換算しています。"
)
