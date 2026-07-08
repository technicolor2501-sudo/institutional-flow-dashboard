# 需給フロー・ダッシュボード

機関投資家が参照する需給・フロー情報を無料公開データから自動取得し、Streamlitで可視化するダッシュボードです。

> **免責事項**: 本ツールは公開データからの情報可視化であり、投資助言ではありません。
> 週次データは数日遅行します。証券会社内部の顧客フロー等は個人では取得不可のため、
> 公開データからの近似・再現です。投資判断はご自身の責任で行ってください。

---

## アーキテクチャ

```
GitHubリポジトリ（private可）
 ├─ fetchers/         データ取得モジュール（ソースごとに1ファイル）
 ├─ processors/       加工・指標計算（GEX計算など）
 ├─ data/             取得データ（parquet形式、リポジトリにコミット）
 ├─ app/              Streamlit ダッシュボード
 ├─ scripts/          実行スクリプト
 └─ .github/workflows GitHub Actions（cron自動実行）
```

- **定期実行**: GitHub Actions の cron（無料枠）
- **フロントエンド**: Streamlit Community Cloud（無料）にデプロイ
- **データ保存**: `data/` ディレクトリに parquet 形式でコミット
- **どこからでも閲覧**: Streamlit の URL をブラウザで開くだけ

---

## フェーズ別実装状況

| フェーズ | 内容 | 状態 |
|----------|------|------|
| **Phase 1 (MVP)** | JPXオプション GEX/DEX + 空売り比率 + SQカレンダー | ✅ 実装済み |
| Phase 2 | 投資部門別フロー (A1/A2) + COT (C1) + VIX (C2) | 🔜 予定 |
| Phase 3 | 曜日別フロー統計 (D4) + 統合シグナル + 残りソース | 🔜 予定 |

---

## セットアップ手順（初心者向け）

### Step 1: GitHubリポジトリを作成

1. [GitHub](https://github.com) にログイン
2. 右上の「+」→「New repository」をクリック
3. Repository name: `institutional-flow-dashboard`
4. Public または Private を選択（どちらでも動作します）
5. 「Create repository」をクリック

### Step 2: コードをプッシュ

```bash
cd institutional-flow-dashboard
git init
git add .
git commit -m "initial: 需給フローダッシュボード Phase 1"
git branch -M main
git remote add origin https://github.com/あなたのユーザー名/institutional-flow-dashboard.git
git push -u origin main
```

### Step 3: GitHub Actions を有効化

1. GitHubリポジトリの「Actions」タブを開く
2. 「I understand my workflows, go ahead and enable them」をクリック
3. 左サイドバー「需給データ自動取得」を選択
4. 「Run workflow」→「Run workflow」で手動テスト実行できます

**シークレット設定**（Phase 2以降で必要):
- リポジトリの「Settings」→「Secrets and variables」→「Actions」
- 「New repository secret」で `FRED_API_KEY` を追加
  - FREDのAPIキーは https://fred.stlouisfed.org/docs/api/api_key.html で無料取得

### Step 4: Streamlit Community Cloud にデプロイ

1. [share.streamlit.io](https://share.streamlit.io) にアクセス（GitHubアカウントでログイン）
2. 「New app」をクリック
3. 以下を入力:
   - **Repository**: `あなたのユーザー名/institutional-flow-dashboard`
   - **Branch**: `main`
   - **Main file path**: `app/dashboard.py`
4. 「Deploy!」をクリック
5. 数分後にURLが発行される（例: `https://あなたのアプリ名.streamlit.app`）

**Streamlit Secrets 設定**（Phase 2以降):
- アプリの「...」メニュー→「Settings」→「Secrets」
- TOML形式で追加:
  ```toml
  FRED_API_KEY = "あなたのFREDキー"
  ```

### Step 5: 別のPC・スマホから閲覧

Streamlit Community Cloud のURLをブラウザで開くだけです。
ログイン不要（Public リポジトリの場合）。

---

## ローカルでの開発・テスト

```bash
# Python 3.11 以上が必要
pip install -r requirements.txt

# データを手動取得
python scripts/run_fetchers.py

# ダッシュボードを起動
streamlit run app/dashboard.py
# → http://localhost:8501 で開く
```

---

## データソース一覧（Phase 1）

| ID | データ | ソース | 更新頻度 | 実装状態 |
|----|--------|--------|----------|----------|
| B1 | 日経225オプション建玉・IV・GEX | JPX公式 | 日次（引け後） | ✅ |
| A3 | 空売り比率 | JPX公式 | 日次（引け後） | ✅ |
| B2 | SQ・OPEXカレンダー | 計算生成 | 静的 | ✅ |
| B3 | 日経VI / 日経225現在値 | yfinance | 日次 | ✅ |

---

## 注意事項

- **データ遅行**: 週次データ（投資部門別・COT）は数日遅れで公表されます。パターン統計・遅行確認用途に限定してください。
- **近似データ**: 本ツールが取得できるのは「公開された集計データ」のみです。証券会社内部の顧客フロー・信託銀行の実際の売買記録は個人では取得不可能です。
- **robots.txt 遵守**: 各フェッチャーは robots.txt を自動確認し、禁止されたURLはクロールしません。
- **レート制限**: リクエスト間に最低1秒の間隔を設けています。
- **JPXのURL変更**: JPXのページ構造は変更されることがあります。一覧ページからリンクを解決する方式を採用していますが、大幅な構造変更時は `fetchers/jpx_options.py` の `_find_data_links` メソッドを更新してください。

---

## ファイル構成

```
institutional-flow-dashboard/
├── fetchers/
│   ├── base.py                 # 共通基底クラス（リトライ・robots.txt・冪等保存）
│   ├── jpx_options.py          # B1: JPX 日経225オプションデータ
│   └── jpx_short_selling.py    # A3: JPX 空売り比率
├── processors/
│   ├── gex_calculator.py       # GEX/DEX/Charm/Vanna 計算（Black-Scholes）
│   └── sq_calendar.py          # SQ・OPEXカレンダー生成
├── data/                       # 取得済みデータ（parquet形式でコミット）
│   ├── jpx_options/
│   ├── jpx_short_selling/
│   ├── gex/
│   ├── sq_calendar/
│   └── run_status.json         # 最終実行ステータス
├── app/
│   ├── dashboard.py            # Streamlit ホームページ
│   └── pages/
│       ├── 1_今日の需給サマリー.py  # 信号機フラグ一覧
│       └── 2_オプション需給.py      # GEX/DEXプロファイル
├── scripts/
│   └── run_fetchers.py         # GitHub Actions エントリーポイント
├── .github/workflows/
│   └── fetch_data.yml          # 自動取得スケジュール（JST換算で1日5回）
├── requirements.txt
└── README.md
```
