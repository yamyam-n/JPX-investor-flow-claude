# JPX 投資部門別売買状況ビューア

日本取引所グループが公表する「投資部門別売買状況（株式・週間）」を、
買い＝上向き／売り＝下向き／差引＝灰色の面グラフで表示する Streamlit アプリ。
スマホの縦画面で読めるようにレイアウトしてある。

```
GitHub Actions（週1回） → JPX から Excel 取得 → data/investor_weekly.csv を更新
                                                        ↓
                     Streamlit アプリが raw.githubusercontent.com から読み込み
```

## 画面

- **週次**：買い・売り・差引の面グラフ。横軸の数字は週末日、縦の区切り線は月替わり、
  下に月・四半期・年のラベル。差引は売買代金に比べて小さいので自動で倍率をかけて表示する（凡例に倍率を表示）。
- **累積差引**：全期間の買い越し・売り越しの積み上がり。
- **データ**：表とCSVダウンロード。
- 市場（東証プライム／スタンダード／グロース など）、投資部門（海外投資家・個人・信託銀行 など）、
  金額／株数、表示期間（26週／52週／全期間）を切り替えられる。

## セットアップ

```bash
git clone https://github.com/USERNAME/jpx-investor-app.git
cd jpx-investor-app
pip install -r requirements.txt
streamlit run app.py
```

同梱の `data/investor_weekly.csv` は **動作確認用のサンプル**（`scripts/make_sample_data.py` が生成した
ダミー値）。実データに置き換えるには次を実行する。

```bash
python scripts/fetch_jpx.py                # 最新の掲載分
python scripts/fetch_jpx.py --years 2025 2026 --all-files   # 過去分もまとめて
```

## GitHub にデータを置く

1. このディレクトリをリポジトリとして push する。
2. Actions タブで `update-jpx-data` を一度手動実行し、`data/investor_weekly.csv` が更新されるか確認する。
   以降は毎週木曜（JST 17:00）に自動更新される。
3. `app.py` の `DEFAULT_DATA_URL` を自分のリポジトリの raw URL に書き換える。
   あるいは `.streamlit/secrets.toml` に次を書く（Streamlit Cloud なら管理画面の Secrets）。

```toml
data_url = "https://raw.githubusercontent.com/USERNAME/jpx-investor-app/main/data/investor_weekly.csv"
```

読み込みは1時間キャッシュされる。GitHub から取れないときは同梱 CSV にフォールバックする。

## スマホで見る

1. [share.streamlit.io](https://share.streamlit.io) でリポジトリを指定してデプロイ（`app.py` を指定）。
2. 発行された URL をスマホで開き、ブラウザの「ホーム画面に追加」でアプリのように使える。
3. iOS Safari / Android Chrome の縦画面（幅 375px 前後）を前提に、
   余白・凡例・目盛りの間引き・タップ操作（ズーム無効、ツールバー非表示）を調整してある。

## データについて

- 出所：日本取引所グループ「投資部門別売買状況」
  https://www.jpx.co.jp/markets/statistics-equities/investor-type/index.html
- 掲載は毎週第4営業日（通常は木曜）15:30。対象は前週の売買。
- CSV は `week_end, week_label, market, investor, basis, sales, purchases, balance` の
  ロング形式。単位は **金額＝百万円、株数＝千株**（アプリ側で億円／百万株に換算）。
- **形式変更の注意**：JPX は 2026年9月29日 掲載分から、株数と金額を1ファイルに統合し、
  直近週のみ収録、1シートに市場×投資部門をまとめる形式に変わる
  （`stock_1_w_YYYYMMDD_YYYYMMDD.xlsx`）。`fetch_jpx.py` は旧形式・新形式の
  両方を読もうとするが、実ファイルのレイアウト依存の処理なので、
  初回は次のように中身を確認して必要なら `tidy_sheet()` を調整すること。

```bash
python scripts/fetch_jpx.py --debug-dump debug_dump/
```

- 利用にあたっては JPX のサイト利用条件に従うこと。取得間隔は常識的な範囲に留める。

## ファイル構成

```
app.py                         Streamlit アプリ
data/investor_weekly.csv       表示用データ（初期状態はサンプル）
scripts/fetch_jpx.py           JPX から取得して CSV に正規化
scripts/make_sample_data.py    サンプルデータ生成
.github/workflows/update-data.yml  週次自動更新
.streamlit/config.toml         ダークテーマ設定
```

投資判断の参考情報であり、正確性は保証しない。
