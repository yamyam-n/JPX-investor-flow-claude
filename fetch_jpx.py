"""JPX「投資部門別売買状況（株式・週間）」を取得して CSV に正規化する。

使い方:
    python scripts/fetch_jpx.py                 # 最新ページから取得して差分更新
    python scripts/fetch_jpx.py --years 2025 2026
    python scripts/fetch_jpx.py --all-files     # 掲載されている全ファイルを取得（遅い）
    python scripts/fetch_jpx.py --debug-dump out/  # Excel の中身をCSVで書き出して確認

出力スキーマ（data/investor_weekly.csv）:
    week_end, week_label, market, investor, basis, sales, purchases, balance
    basis="金額" の単位は百万円、basis="株数" の単位は千株。

注意:
    JPX は 2026年9月29日 掲載分からファイル形式を変更する（株数・金額を1ファイルに統合、
    直近週のみ収録、市場×投資部門の1シート）。本スクリプトは旧形式
    (stock_val_1_YYMMWW.xls / stock_vol_1_YYMMWW.xls) と
    新形式 (stock_1_w_YYYYMMDD_YYYYMMDD.xlsx) の両方を読もうとするが、
    レイアウト依存の処理のため、初回は --debug-dump で中身を確認することを勧める。
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT_CSV = ROOT / "data" / "investor_weekly.csv"

BASE = "https://www.jpx.co.jp"
INDEX_URL = f"{BASE}/markets/statistics-equities/investor-type/index.html"
HEADERS = {"User-Agent": "jpx-investor-app/1.0 (personal use)"}
TIMEOUT = 60

MEASURES = {"売り": "sales", "買い": "purchases", "差引": "balance"}
WEEK_RE = re.compile(r"(\d{4})年\s*(\d{1,2})月第(\d)週")
RANGE_RE = re.compile(r"(\d{1,2})月(\d{1,2})日\s*[～~-]\s*(\d{1,2})月(\d{1,2})日")
OLD_FILE_RE = re.compile(r"stock_(val|vol)_1_(\d{2})(\d{2})(\d{2})\.xlsx?$", re.I)
NEW_FILE_RE = re.compile(r"stock_1_w_(\d{8})_(\d{8})\.xlsx?$", re.I)


def norm(x) -> str:
    """全角空白などを潰した比較用の文字列にする。"""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return ""
    s = unicodedata.normalize("NFKC", str(x))
    return re.sub(r"\s+", "", s).strip()


# ------------------------------------------------------------------ 一覧ページ


@dataclass
class WeekEntry:
    label: str          # 2026年8月第4週
    end: pd.Timestamp   # 2026-08-28
    links: list[str]    # そのの週の Excel URL


def fetch(url: str) -> bytes:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.content


def listing_pages(years: list[str] | None) -> list[str]:
    """一覧ページ（最新＋バックナンバー）の URL を集める。"""
    html = fetch(INDEX_URL).decode("utf-8", "ignore")
    soup = BeautifulSoup(html, "html.parser")
    pages = [INDEX_URL]
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "investor-type" in href and "archives" in href:
            url = urljoin(INDEX_URL, href)
            text = norm(a.get_text())
            if years and not any(y in text or y in href for y in years):
                continue
            if url not in pages:
                pages.append(url)
    return pages


def parse_listing(url: str) -> list[WeekEntry]:
    """一覧ページの表から、週ラベル・週末日・Excel リンクを取り出す。"""
    html = fetch(url).decode("utf-8", "ignore")
    soup = BeautifulSoup(html, "html.parser")
    entries: list[WeekEntry] = []

    for tr in soup.find_all("tr"):
        text = norm(tr.get_text(" "))
        m = WEEK_RE.search(text)
        if not m:
            continue
        year, month = int(m.group(1)), int(m.group(2))
        label = f"{year}年{month}月第{m.group(3)}週"

        end = None
        rm = RANGE_RE.search(text)
        if rm:
            end_month, end_day = int(rm.group(3)), int(rm.group(4))
            end_year = year + 1 if end_month < month else year
            end = pd.Timestamp(end_year, end_month, end_day)

        links = [
            urljoin(url, a["href"])
            for a in tr.find_all("a", href=True)
            if a["href"].lower().endswith((".xls", ".xlsx"))
        ]
        if links:
            entries.append(WeekEntry(label=label, end=end, links=links))
    return entries


def nth_friday(year: int, month: int, nth: int) -> pd.Timestamp:
    """週末日が取れなかったときの保険（月内 n 番目の金曜日）。"""
    first = pd.Timestamp(year, month, 1)
    offset = (4 - first.weekday()) % 7
    return first + pd.Timedelta(days=offset + 7 * (nth - 1))


def label_to_date(label: str, table: dict[str, pd.Timestamp]) -> pd.Timestamp | None:
    if label in table and table[label] is not None:
        return table[label]
    m = WEEK_RE.search(label)
    if not m:
        return None
    return nth_friday(int(m.group(1)), int(m.group(2)), int(m.group(3)))


# ------------------------------------------------------------------ Excel 解析


def read_sheets(content: bytes, filename: str) -> dict[str, pd.DataFrame]:
    engine = "xlrd" if filename.lower().endswith(".xls") else "openpyxl"
    return pd.read_excel(io.BytesIO(content), sheet_name=None, header=None, engine=engine)


def find_measure_axis(g: pd.DataFrame) -> tuple[str, int]:
    """売り／買い／差引 が横並びか縦並びかを判定し、その行（列）番号を返す。"""
    best = None
    for i in range(g.shape[0]):
        hits = sum(1 for v in g.iloc[i] if any(k in norm(v) for k in MEASURES))
        if hits >= 2 and (best is None or hits > best[2]):
            best = ("row", i, hits)
    for j in range(g.shape[1]):
        hits = sum(1 for v in g.iloc[:, j] if any(k in norm(v) for k in MEASURES))
        if hits >= 2 and (best is None or hits > best[2]):
            best = ("col", j, hits)
    if best is None:
        raise ValueError("売り／買い／差引 の見出しが見つかりません")
    return best[0], best[1]


def measure_of(cell) -> str | None:
    s = norm(cell)
    for jp, en in MEASURES.items():
        if jp in s:
            return en
    return None


def header_labels(g: pd.DataFrame, axis: str, idx: int) -> list[str]:
    """売買区分の見出しの1つ手前にある「投資部門」見出しを拾って前方補完する。"""
    for back in range(1, 5):
        k = idx - back
        if k < 0:
            break
        line = g.iloc[k] if axis == "row" else g.iloc[:, k]
        vals = [norm(v) for v in line]
        named = [v for v in vals if v and not measure_of(v) and not v.isdigit()]
        if len(named) >= 2:
            out, last = [], ""
            for v in vals:
                if v:
                    last = v
                out.append(last)
            return out
    return ["" for _ in range(g.shape[1] if axis == "row" else g.shape[0])]


def tidy_sheet(g: pd.DataFrame) -> pd.DataFrame:
    """1シートを long 形式（key, investor, measure, value）に変換する。

    key は行見出し（週ラベル or 市場名）。呼び出し側で意味づけする。
    """
    axis, idx = find_measure_axis(g)
    if axis == "col":  # 売買区分が縦に並ぶ場合は転置して同じ処理に落とす
        g = g.T.reset_index(drop=True)
        g.columns = range(g.shape[1])
        axis, idx = find_measure_axis(g)

    investors = header_labels(g, "row", idx)
    measures = {j: measure_of(g.iat[idx, j]) for j in range(g.shape[1])}
    measure_cols = {j: m for j, m in measures.items() if m}
    if not measure_cols:
        raise ValueError("売買区分の列が特定できません")

    # 行見出しの列 = 売買区分より左で、文字列が最も多く入っている列
    left_cols = [j for j in range(min(measure_cols))] or [0]
    key_col = max(left_cols, key=lambda j: sum(1 for v in g.iloc[idx + 1 :, j] if norm(v)))

    rows = []
    for i in range(idx + 1, g.shape[0]):
        key = norm(g.iat[i, key_col])
        if not key:
            continue
        for j, meas in measure_cols.items():
            val = pd.to_numeric(g.iat[i, j], errors="coerce")
            if pd.isna(val):
                continue
            inv = investors[j] if j < len(investors) else ""
            rows.append({"key": key, "investor": inv, "measure": meas, "value": float(val)})
    return pd.DataFrame(rows)


def clean_investor(name: str) -> str:
    s = norm(name)
    s = re.sub(r"^(投資部門|部門)", "", s)
    return s


def parse_old_file(content: bytes, filename: str, basis: str,
                   dates: dict[str, pd.Timestamp]) -> pd.DataFrame:
    """旧形式: 市場ごとにシート、行に週。ファイル1つで年初来の全週が入る。"""
    out = []
    for sheet, g in read_sheets(content, filename).items():
        try:
            t = tidy_sheet(g)
        except ValueError:
            continue
        t = t[t["key"].str.contains("週")]
        if t.empty:
            continue
        t["market"] = norm(sheet)
        t["week_label"] = t["key"].str.replace(r"\(.*?\)", "", regex=True)
        out.append(t)
    if not out:
        return pd.DataFrame()

    df = pd.concat(out, ignore_index=True)
    wide = df.pivot_table(
        index=["week_label", "market", "investor"], columns="measure", values="value"
    ).reset_index()
    wide["basis"] = basis
    wide["week_end"] = wide["week_label"].map(lambda s: label_to_date(s, dates))
    return wide


def parse_new_file(content: bytes, filename: str,
                   week_end: pd.Timestamp, week_label: str) -> pd.DataFrame:
    """新形式（2026-09-29〜）: 1ファイル1週、行に市場、列に投資部門。"""
    frames = []
    for sheet, g in read_sheets(content, filename).items():
        try:
            t = tidy_sheet(g)
        except ValueError:
            continue
        if t.empty:
            continue
        basis = "株数" if "株数" in norm(sheet) or "口数" in norm(sheet) else "金額"
        t = t.rename(columns={"key": "market"})
        t["basis"] = basis
        frames.append(t)
    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    wide = df.pivot_table(
        index=["market", "investor", "basis"], columns="measure", values="value"
    ).reset_index()
    wide["week_end"] = week_end
    wide["week_label"] = week_label
    return wide


# ------------------------------------------------------------------ 実行


def collect(years: list[str] | None, all_files: bool, debug_dump: Path | None) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []

    for page in listing_pages(years):
        entries = parse_listing(page)
        if not entries:
            continue
        dates = {e.label: e.end for e in entries}
        print(f"[page] {page} — {len(entries)} 週")

        targets = entries if all_files else entries[:1]  # 旧形式は最新1本に年初来が入る
        new_format = [e for e in entries if any(NEW_FILE_RE.search(u) for u in e.links)]
        if new_format:
            targets = new_format  # 新形式は1ファイル1週なので全部要る

        seen: set[str] = set()
        for entry in targets:
            for url in entry.links:
                name = url.rsplit("/", 1)[-1]
                if name in seen:
                    continue
                seen.add(name)
                old = OLD_FILE_RE.search(name)
                new = NEW_FILE_RE.search(name)
                if not (old or new):
                    continue
                try:
                    content = fetch(url)
                except Exception as e:
                    print(f"  ! 取得失敗 {name}: {e}", file=sys.stderr)
                    continue

                if debug_dump:
                    debug_dump.mkdir(parents=True, exist_ok=True)
                    for sheet, g in read_sheets(content, name).items():
                        g.to_csv(debug_dump / f"{name}_{sheet}.csv", index=False, header=False)
                    print(f"  · dump {name}")

                try:
                    if old:
                        basis = "金額" if old.group(1).lower() == "val" else "株数"
                        df = parse_old_file(content, name, basis, dates)
                    else:
                        end = pd.Timestamp(new.group(2))
                        df = parse_new_file(content, name, end, entry.label)
                except Exception as e:
                    print(f"  ! 解析失敗 {name}: {e}", file=sys.stderr)
                    continue

                if df.empty:
                    print(f"  ! データなし {name}", file=sys.stderr)
                    continue
                print(f"  + {name}: {len(df)} 行")
                frames.append(df)

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    for c in ("sales", "purchases", "balance"):
        if c not in df:
            df[c] = pd.NA
    df["balance"] = df["balance"].fillna(df["purchases"] - df["sales"])
    df["investor"] = df["investor"].map(clean_investor)
    df = df.dropna(subset=["week_end", "market", "investor"])
    df = df[df["investor"] != ""]
    return df[["week_end", "week_label", "market", "investor", "basis",
               "sales", "purchases", "balance"]]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", nargs="*", help="対象年（例: 2025 2026）。既定は最新ページのみ")
    ap.add_argument("--all-files", action="store_true", help="掲載されている全ファイルを取得")
    ap.add_argument("--debug-dump", type=Path, help="Excel の中身を CSV で書き出す")
    ap.add_argument("--out", type=Path, default=OUT_CSV)
    args = ap.parse_args()

    new = collect(args.years, args.all_files, args.debug_dump)
    if new.empty:
        print("取得できたデータがありません。既存の CSV は変更しません。", file=sys.stderr)
        return 1

    new["week_end"] = pd.to_datetime(new["week_end"]).dt.strftime("%Y-%m-%d")

    if args.out.exists():
        old = pd.read_csv(args.out)
        merged = pd.concat([old, new], ignore_index=True)
    else:
        merged = new

    merged = (
        merged.drop_duplicates(subset=["week_end", "market", "investor", "basis"], keep="last")
        .sort_values(["week_end", "market", "investor", "basis"])
        .reset_index(drop=True)
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(args.out, index=False)
    print(f"wrote {args.out}: {len(merged):,} 行 / {merged['week_end'].nunique()} 週")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
