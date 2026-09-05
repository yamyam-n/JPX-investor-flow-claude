"""JPX「投資部門別売買状況（株式・週間）」を取得して CSV に正規化する。

実ファイルの構造（2026年9月時点の stock_val_1_YYMMWW.xls / stock_vol_1_YYMMWW.xls）:
    ・シートは市場ごと（TSE Prime / TSE Standard / TSE Growth / Tokyo & Nagoya）
    ・1ファイルに前週と当週の2週分が横に並ぶ
    ・投資部門は「売り／買い／合計」の3行1組。差引きは「買い」の行に入る
    ・数値はカンマ区切りの文字列。金額の単位は千円、株数の単位は千株

使い方:
    python scripts/fetch_jpx.py                     # 最新ページの未取得分だけ
    python scripts/fetch_jpx.py --years 2026 2025   # バックナンバーも
    python scripts/fetch_jpx.py --basis 金額         # 金額だけ（取得量を半分に）
    python scripts/fetch_jpx.py --refetch           # 取得済みの週もやり直す
    python scripts/fetch_jpx.py --debug-dump dump/  # Excel の中身を CSV で確認

出力（data/investor_weekly.csv）:
    week_end, week_label, market, investor, basis, sales, purchases, balance
    金額は百万円、株数は千株に揃える。
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

BASE = "https://www.jpx.co.jp"
INDEX_URL = f"{BASE}/markets/statistics-equities/investor-type/index.html"
ARCHIVE_TMPL = f"{BASE}/markets/statistics-equities/investor-type/00-00-archives-{{:02d}}.html"
HEADERS = {"User-Agent": "jpx-investor-app/1.0 (personal use)"}
TIMEOUT = 60
SLEEP = 1.5  # JPX は高負荷な自動取得を控えるよう求めているので間隔を空ける

WEEK_RE = re.compile(r"(\d{4})年\s*(\d{1,2})月第(\d)週")
SPAN_RE = re.compile(r"(\d{1,2})[／/](\d{1,2})\s*[～~\-−–—]\s*(\d{1,2})[／/](\d{1,2})")
RANGE_JP_RE = re.compile(r"(\d{1,2})月(\d{1,2})日\s*[～~\-−–—]\s*(\d{1,2})月(\d{1,2})日")
OLD_FILE_RE = re.compile(r"stock_(val|vol)_1_(\d{6})\.xlsx?$", re.I)
NEW_FILE_RE = re.compile(r"stock_1_w_(\d{8})_(\d{8})\.xlsx?$", re.I)

SHEET_MARKET = {
    "tseprime": "東証プライム",
    "tsestandard": "東証スタンダード",
    "tsegrowth": "東証グロース",
    "tokyo&nagoya": "二市場計",
    "tokyoandnagoya": "二市場計",
}

# 単位 → 「金額なら百万円 / 株数なら千株」に揃えるための倍率
UNIT_FACTOR = {"千円": 0.001, "百万円": 1.0, "円": 0.000001, "千株": 1.0, "百株": 0.1, "株": 0.001}


def norm(x) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return ""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(x))).strip()


def to_num(v) -> float | None:
    """'4,320,838,305' や '△1,234' を数値にする。"""
    s = norm(v)
    if not s or s in {"-", "‐", "―", "–", "—", "△", "▲"}:
        return None
    neg = s[0] in "△▲-"
    s = s.lstrip("△▲-").replace(",", "").rstrip("%")
    try:
        x = float(s)
    except ValueError:
        return None
    return -x if neg else x


def find_root() -> Path:
    """scripts/ 直下でもリポジトリ直下でも動くように出力先を決める。"""
    here = Path(__file__).resolve().parent
    for cand in (here.parent, here, Path.cwd()):
        if (cand / "app.py").exists():
            return cand
    return Path.cwd()


# ------------------------------------------------------------------ 一覧ページ


@dataclass
class WeekEntry:
    label: str
    end: pd.Timestamp | None
    links: list[str] = field(default_factory=list)


def fetch(url: str) -> bytes:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.content


def listing_pages(years: list[str] | None) -> list[str]:
    pages = [INDEX_URL]
    if not years:
        return pages

    html = fetch(INDEX_URL).decode("utf-8", "ignore")
    soup = BeautifulSoup(html, "html.parser")
    found = []
    for a in soup.find_all("a", href=True):
        text = norm(a.get_text())
        if re.fullmatch(r"\d{4}年", text) and text[:4] in years:
            found.append(urljoin(INDEX_URL, a["href"]))
    if found:
        return pages + found

    # リンクが JS 生成などで拾えない場合は既知の URL 形式を順に当たる
    # （00 が当年、01 が前年…という並び。年が変わると番号がずれる）
    print("バックナンバーのリンクが拾えないため URL を探索します…")
    remaining = set(years)
    for i in range(0, 14):
        if not remaining:
            break
        url = ARCHIVE_TMPL.format(i)
        try:
            body = fetch(url).decode("utf-8", "ignore")
        except Exception:
            continue
        hit = {m[0] for m in WEEK_RE.findall(body)} & remaining
        if hit:
            print(f"  {url} → {'、'.join(sorted(hit))}年")
            pages.append(url)
            remaining -= hit
        time.sleep(SLEEP)
    if remaining:
        print(f"  見つからなかった年: {'、'.join(sorted(remaining))}", file=sys.stderr)
    return pages


def parse_listing(url: str) -> list[WeekEntry]:
    html = fetch(url).decode("utf-8", "ignore")
    soup = BeautifulSoup(html, "html.parser")
    entries: list[WeekEntry] = []
    for tr in soup.find_all("tr"):
        text = norm(tr.get_text(" "))
        m = WEEK_RE.search(text)
        if not m:
            continue
        year, month = int(m.group(1)), int(m.group(2))
        end = None
        rm = RANGE_JP_RE.search(text)
        if rm:
            em, ed = int(rm.group(3)), int(rm.group(4))
            end = pd.Timestamp(year + (1 if em < month else 0), em, ed)
        links = [
            urljoin(url, a["href"])
            for a in tr.find_all("a", href=True)
            if a["href"].lower().endswith((".xls", ".xlsx"))
        ]
        if links:
            entries.append(
                WeekEntry(label=f"{year}年{month}月第{m.group(3)}週", end=end, links=links)
            )
    return entries


def label_for(end: pd.Timestamp, table: dict[pd.Timestamp, str]) -> str:
    if end in table:
        return table[end]
    return f"{end.year}年{end.month}月第{(end.day - 1) // 7 + 1}週"


# ------------------------------------------------------------------ Excel 解析


def read_sheets(content: bytes, filename: str) -> dict[str, pd.DataFrame]:
    engine = "xlrd" if filename.lower().endswith(".xls") else "openpyxl"
    return pd.read_excel(io.BytesIO(content), sheet_name=None, header=None, engine=engine)


def sheet_market(g: pd.DataFrame, sheet_name: str) -> str:
    head = " ".join(norm(v) for v in g.iloc[:3].to_numpy().ravel() if norm(v))
    m = re.search(r"売買状況(.+?)[\[［]", head)
    if m and m.group(1):
        return m.group(1)
    key = norm(sheet_name).lower().replace(" ", "")
    return SHEET_MARKET.get(key, norm(sheet_name))


def sheet_unit(g: pd.DataFrame) -> float:
    head = " ".join(norm(v) for v in g.iloc[:12].to_numpy().ravel() if norm(v))
    for unit in ("百万円", "千円", "千株", "百株"):
        if unit in head:
            return UNIT_FACTOR[unit]
    return 1.0


def numeric_count(g: pd.DataFrame, col: int, start: int) -> int:
    if col >= g.shape[1]:
        return -1
    return sum(1 for i in range(start, g.shape[0]) if to_num(g.iat[i, col]) is not None)


def pick_col(g: pd.DataFrame, header_col: int, start: int) -> int:
    """見出しセルが結合されている場合、数値が入っている隣の列を選ぶ。"""
    if numeric_count(g, header_col + 1, start) > numeric_count(g, header_col, start):
        return header_col + 1
    return header_col


def parse_old_sheet(g: pd.DataFrame, sheet_name: str, basis: str,
                    labels: dict[pd.Timestamp, str]) -> list[dict]:
    """1シート（＝1市場）から2週分を取り出す。"""
    market = sheet_market(g, sheet_name)
    factor = sheet_unit(g)

    # 年月は「2026年8月第4週 …」の見出しから
    year = month = None
    for i in range(min(10, g.shape[0])):
        for j in range(g.shape[1]):
            m = WEEK_RE.search(norm(g.iat[i, j]))
            if m:
                year, month = int(m.group(1)), int(m.group(2))
                break
        if year:
            break
    if year is None:
        return []

    # 期間の見出し行（08/17～08/21 が並ぶ行）を探す
    week_row, week_cols = None, []
    for i in range(g.shape[0]):
        hits = []
        for j in range(g.shape[1]):
            m = SPAN_RE.search(norm(g.iat[i, j]))
            if m:
                em, ed = int(m.group(3)), int(m.group(4))
                y = year - 1 if em > month + 1 else year
                hits.append((j, pd.Timestamp(y, em, ed)))
        if hits and len(hits) >= len(week_cols):
            week_row, week_cols = i, hits
    if week_row is None:
        return []

    head_row = week_row + 1
    data_start = head_row + 1

    # 「売り／買い／合計」が入る列
    side_col = max(
        range(min(4, g.shape[1])),
        key=lambda j: sum(
            1
            for i in range(data_start, g.shape[0])
            if norm(g.iat[i, j]) in {"売り", "買い", "合計"}
        ),
    )

    rows: list[dict] = []
    for k, (wcol, end) in enumerate(week_cols):
        stop = week_cols[k + 1][0] if k + 1 < len(week_cols) else g.shape[1]
        val_col = bal_col = None
        for j in range(wcol, stop):
            h = norm(g.iat[head_row, j])
            if not h:
                continue
            if val_col is None and re.search(r"金額|株数|Value|Volume", h, re.I):
                val_col = pick_col(g, j, data_start)
            elif bal_col is None and re.search(r"差引|Balance", h, re.I):
                bal_col = pick_col(g, j, data_start)
        if val_col is None:
            continue

        label = label_for(end, labels)
        investor, sell = "", None
        for i in range(data_start, g.shape[0]):
            side = norm(g.iat[i, side_col])
            if side == "売り":
                name = norm(g.iat[i, 0])
                if name:
                    investor = name
                sell = to_num(g.iat[i, val_col])
            elif side == "買い" and investor:
                buy = to_num(g.iat[i, val_col])
                if buy is None or sell is None:
                    continue
                bal = to_num(g.iat[i, bal_col]) if bal_col is not None else None
                if bal is None:
                    bal = buy - sell
                rows.append(
                    {
                        "week_end": end,
                        "week_label": label,
                        "market": market,
                        "investor": investor,
                        "basis": basis,
                        "sales": round(sell * factor, 3),
                        "purchases": round(buy * factor, 3),
                        "balance": round(bal * factor, 3),
                    }
                )
    return rows


def parse_old_file(content: bytes, filename: str, basis: str,
                   labels: dict[pd.Timestamp, str]) -> pd.DataFrame:
    rows: list[dict] = []
    for sheet, g in read_sheets(content, filename).items():
        try:
            rows += parse_old_sheet(g, sheet, basis, labels)
        except Exception as e:
            print(f"  ! シート {sheet}: {e}", file=sys.stderr)
    return pd.DataFrame(rows)


def parse_new_file(content: bytes, filename: str, end: pd.Timestamp,
                   label: str) -> pd.DataFrame:
    """新形式（2026年9月29日掲載分〜）: 1ファイル1週、縦に市場・横に投資部門。

    実ファイルで未検証。ずれる場合は --debug-dump で中身を確認して調整すること。
    """
    rows: list[dict] = []
    for sheet, g in read_sheets(content, filename).items():
        basis = "株数" if re.search(r"株数|Volume", norm(sheet), re.I) else "金額"
        factor = sheet_unit(g)

        head_row = None
        for i in range(g.shape[0]):
            hits = sum(
                1 for v in g.iloc[i] if re.search(r"売り|買い|Sales|Purchases", norm(v), re.I)
            )
            if hits >= 2:
                head_row = i
                break
        if head_row is None or head_row == 0:
            continue

        investors, last = [], ""
        for v in g.iloc[head_row - 1]:
            s = norm(v)
            if s:
                last = s
            investors.append(last)

        for i in range(head_row + 1, g.shape[0]):
            market = norm(g.iat[i, 0])
            if not market:
                continue
            acc: dict[str, dict] = {}
            for j in range(1, g.shape[1]):
                val = to_num(g.iat[i, j])
                if val is None or j >= len(investors):
                    continue
                h = norm(g.iat[head_row, j])
                key = investors[j]
                if re.search(r"売り|Sales", h, re.I):
                    acc.setdefault(key, {})["sales"] = val * factor
                elif re.search(r"買い|Purchases", h, re.I):
                    acc.setdefault(key, {})["purchases"] = val * factor
                elif re.search(r"差引|Balance", h, re.I):
                    acc.setdefault(key, {})["balance"] = val * factor
            for inv, d in acc.items():
                if "sales" in d and "purchases" in d:
                    rows.append(
                        {
                            "week_end": end,
                            "week_label": label,
                            "market": market,
                            "investor": inv,
                            "basis": basis,
                            "sales": round(d["sales"], 3),
                            "purchases": round(d["purchases"], 3),
                            "balance": round(d.get("balance", d["purchases"] - d["sales"]), 3),
                        }
                    )
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ 実行


def collect(args, have: set[str]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    want = {"金額": args.basis in (None, "金額"), "株数": args.basis in (None, "株数")}

    for page in listing_pages(args.years):
        try:
            entries = parse_listing(page)
        except Exception as e:
            print(f"! ページ取得失敗 {page}: {e}", file=sys.stderr)
            continue
        if not entries:
            continue
        labels = {e.end: e.label for e in entries if e.end is not None}
        print(f"[page] {page} — {len(entries)} 週")

        for entry in entries:
            if not args.refetch and entry.end is not None:
                if entry.end.strftime("%Y-%m-%d") in have:
                    continue
            for url in entry.links:
                name = url.rsplit("/", 1)[-1]
                old, new = OLD_FILE_RE.search(name), NEW_FILE_RE.search(name)
                if not (old or new):
                    continue
                basis = None
                if old:
                    basis = "金額" if old.group(1).lower() == "val" else "株数"
                    if not want[basis]:
                        continue
                try:
                    content = fetch(url)
                    time.sleep(SLEEP)
                except Exception as e:
                    print(f"  ! 取得失敗 {name}: {e}", file=sys.stderr)
                    continue

                if args.debug_dump:
                    args.debug_dump.mkdir(parents=True, exist_ok=True)
                    for sheet, g in read_sheets(content, name).items():
                        g.to_csv(args.debug_dump / f"{name}_{sheet}.csv", index=False, header=False)

                try:
                    if old:
                        df = parse_old_file(content, name, basis, labels)
                    else:
                        df = parse_new_file(content, name, pd.Timestamp(new.group(2)), entry.label)
                except Exception as e:
                    print(f"  ! 解析失敗 {name}: {e}", file=sys.stderr)
                    continue

                if df.empty:
                    print(f"  ! データなし {name}", file=sys.stderr)
                    continue
                weeks = sorted({d.strftime("%m/%d") for d in df["week_end"]})
                print(f"  + {name}: {len(df)} 行（週末 {', '.join(weeks)}）")
                frames.append(df)

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def main() -> int:
    root = find_root()
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", nargs="*", help="バックナンバーの年（例: 2026 2025）")
    ap.add_argument("--basis", choices=["金額", "株数"], help="どちらか一方だけ取得")
    ap.add_argument("--refetch", action="store_true", help="取得済みの週もやり直す")
    ap.add_argument("--debug-dump", type=Path, help="Excel の中身を CSV で書き出す")
    ap.add_argument("--out", type=Path, default=root / "data" / "investor_weekly.csv")
    args = ap.parse_args()

    old = pd.read_csv(args.out) if args.out.exists() else pd.DataFrame()
    have = set(old["week_end"].astype(str)) if "week_end" in old else set()

    new = collect(args, have)
    if new.empty:
        print("取得できたデータがありません。既存の CSV は変更しません。", file=sys.stderr)
        return 1
    new["week_end"] = pd.to_datetime(new["week_end"]).dt.strftime("%Y-%m-%d")

    merged = pd.concat([old, new], ignore_index=True) if len(old) else new
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
