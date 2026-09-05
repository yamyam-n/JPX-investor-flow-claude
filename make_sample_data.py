"""動作確認用のサンプルデータを生成する。

実データ (scripts/fetch_jpx.py) を取得するまでのプレースホルダ。
出力: data/investor_weekly.csv （fetch_jpx.py と同じスキーマ）
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parents[1] / "data" / "investor_weekly.csv"

MARKETS = ["東証プライム", "東証スタンダード", "東証グロース", "二市場計"]
INVESTORS = [
    "自己計",
    "海外投資家",
    "個人",
    "投資信託",
    "事業法人",
    "信託銀行",
    "生保・損保",
    "都銀・地銀等",
    "その他金融機関",
    "その他法人等",
    "証券会社",
]

# 市場規模の目安（週間売買代金・百万円）と部門別シェア
MARKET_SCALE = {
    "東証プライム": 20_000_000,
    "東証スタンダード": 900_000,
    "東証グロース": 700_000,
    "二市場計": 21_600_000,
}
SHARE = {
    "自己計": 0.24,
    "海外投資家": 0.62,
    "個人": 0.20,
    "投資信託": 0.03,
    "事業法人": 0.02,
    "信託銀行": 0.05,
    "生保・損保": 0.004,
    "都銀・地銀等": 0.003,
    "その他金融機関": 0.008,
    "その他法人等": 0.004,
    "証券会社": 0.012,
}


def week_ends(start: dt.date, end: dt.date) -> list[dt.date]:
    """金曜日を週末として週次の日付列を作る。"""
    d = start + dt.timedelta(days=(4 - start.weekday()) % 7)
    out = []
    while d <= end:
        out.append(d)
        d += dt.timedelta(days=7)
    return out


def week_label(d: dt.date) -> str:
    """2026年8月第4週 のような週ラベル。月内の金曜の並び順で数える。"""
    nth = (d.day - 1) // 7 + 1
    return f"{d.year}年{d.month}月第{nth}週"


def main() -> None:
    rng = np.random.default_rng(20260101)
    dates = week_ends(dt.date(2025, 1, 1), dt.date(2026, 8, 28))

    rows = []
    # 部門ごとに自己相関のあるフローを作り、それらしい波形にする
    net_state = {(m, i): 0.0 for m in MARKETS for i in INVESTORS}
    for d in dates:
        # 週ごとの市場全体の活況度
        activity = float(np.clip(rng.normal(1.0, 0.18), 0.55, 1.7))
        for m in MARKETS:
            for inv in INVESTORS:
                base = MARKET_SCALE[m] * SHARE[inv] * activity
                gross = base * float(np.clip(rng.normal(1.0, 0.12), 0.6, 1.5))
                # ネットは平均回帰するランダムウォーク（対売買代金比 ±3% 程度）
                prev = net_state[(m, inv)]
                shock = rng.normal(0, 0.014)
                nxt = float(np.clip(prev * 0.55 + shock, -0.045, 0.045))
                net_state[(m, inv)] = nxt
                net = gross * nxt
                purchases = (gross + net) / 2
                sales = (gross - net) / 2
                rows.append(
                    {
                        "week_end": d.isoformat(),
                        "week_label": week_label(d),
                        "market": m,
                        "investor": inv,
                        "basis": "金額",
                        "sales": round(sales),
                        "purchases": round(purchases),
                        "balance": round(purchases - sales),
                    }
                )
                # 株数（千株）: 平均株価 2,500 円と仮定して概算
                rows.append(
                    {
                        "week_end": d.isoformat(),
                        "week_label": week_label(d),
                        "market": m,
                        "investor": inv,
                        "basis": "株数",
                        "sales": round(sales * 1000 / 2500),
                        "purchases": round(purchases * 1000 / 2500),
                        "balance": round((purchases - sales) * 1000 / 2500),
                    }
                )

    df = pd.DataFrame(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False)
    print(f"wrote {OUT} ({len(df):,} rows, {df['week_end'].nunique()} weeks) [SAMPLE DATA]")


if __name__ == "__main__":
    main()
