# デプロイ手順

## 前提条件
- GitHub アカウント
- Streamlit Community Cloud アカウント（無料: https://share.streamlit.io）
- GitHub CLI (`gh`) がインストール済み

---

## Step 1: GitHub リポジトリ作成・プッシュ

```bash
# リポジトリをプライベートで作成（公開してよければ --public）
gh repo create institutional-flow-dashboard --private --source=. --remote=origin --push

# 初回プッシュ後、データを一度手動取得
python scripts/run_fetchers.py
git add data/
git commit -m "data: 初回データ取得"
git push
```

## Step 2: GitHub Actions の有効化

リポジトリの **Settings → Actions → General** で
"Allow all actions and reusable workflows" を選択。

その後、手動でワークフローを実行:
```bash
gh workflow run "需給データ自動取得"
```

ワークフローの実行状況を確認:
```bash
gh run list --workflow=fetch_data.yml
gh run watch  # 最新の実行をライブ表示
```

## Step 3: Streamlit Community Cloud へデプロイ

1. https://share.streamlit.io にアクセス（GitHub アカウントでログイン）
2. **"New app"** をクリック
3. 以下を設定:
   - **Repository**: `your-username/institutional-flow-dashboard`
   - **Branch**: `main`
   - **Main file path**: `app/dashboard.py`
4. **"Deploy!"** をクリック

デプロイ後は `https://your-username-institutional-flow-dashboard.streamlit.app` でアクセス可能。

## データ更新スケジュール（GitHub Actions）

| JST | UTC | 目的 |
|-----|-----|------|
| 08:30 平日 | 23:30 前日 | 前日JPX終値データ |
| 15:30 平日 | 06:30 | 当日JPX取引終了後 |
| 19:00 平日 | 10:00 | OSE日次レポートZIP公開後 |
| 20:00 木曜 | 11:00 | 週次統計集中公表 |
| 23:00 平日 | 14:00 | 空売りPDF公開後 |

## Secrets 設定（現在不要）

Phase 1 は公開データのみのため、Secrets 不要。
Phase 2 以降で FRED API キーが必要な場合:
- GitHub: Settings → Secrets → Actions → `FRED_API_KEY`
- Streamlit: App Settings → Secrets → `[fred]\napi_key = "xxx"`

## トラブルシューティング

### Actions が失敗する場合
```bash
gh run list --workflow=fetch_data.yml --limit 5
gh run view <run-id> --log
```

### データが更新されない場合
```bash
# 手動で強制実行
gh workflow run "需給データ自動取得" -f force=true
```

### Streamlit でエラーが出る場合
- アプリのログを確認（Streamlit Cloud の "Manage app" → "Logs"）
- `data/run_status.json` の内容を確認
