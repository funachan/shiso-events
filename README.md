# 宍粟市 地域イベントカレンダー

船元りょうこ公式サイト（[ryoko-funamoto.jp](https://ryoko-funamoto.jp/)）と連携する、宍粟市の地域イベント情報サイトです。

## 公開URL

```
https://funachan.github.io/shiso-events/
```

## イベントデータの管理

### 手動でイベントを追加する

`data/events.json` を編集してください。

```json
{
  "id": "2026-001",
  "title": "イベント名",
  "date": "2026-08-15",
  "time": "10:00",
  "end_time": "15:00",
  "location": "場所",
  "category": "お祭り",
  "description": "説明文",
  "url": "https://申し込みURL",
  "source": "manual",
  "status": "approved"
}
```

**`status` の値：**
- `"approved"` → サイトに表示される
- `"draft"` → 下書き（表示されない）

**カテゴリ：** `お祭り` / `マルシェ` / `講座` / `スポーツ` / `文化` / `その他`

### 自動収集（GitHub Actions）

毎週月曜 9:00 AM（JST）に自動で宍粟市公式サイトからイベントを収集し、Pull Request を作成します。

**PR を受け取ったら：**
1. PR の差分を確認する
2. 不要なイベントは行ごと削除
3. 公開するイベントの `"status"` を `"draft"` → `"approved"` に変更
4. PR をマージ → 約30秒で自動デプロイ

### 手動で収集を実行する

GitHub リポジトリの `Actions` タブ → `イベント自動収集` → `Run workflow`

## WordPress への埋め込み

```html
<iframe src="https://funachan.github.io/shiso-events/"
        width="100%" height="700" frameborder="0"
        title="宍粟市イベントカレンダー"></iframe>
```
