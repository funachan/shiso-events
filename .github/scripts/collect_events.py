#!/usr/bin/env python3
"""
宍粟市公式サイトからイベントを収集し、
WordPress REST API 経由で「下書き」として投稿するスクリプト。
同じタイトル+日付のイベントは重複投稿しない。
"""

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup

JST = timezone(timedelta(hours=9))

# ── 環境変数（GitHub Secrets から注入） ──────────────────────────
WP_URL      = os.environ["WP_URL"].rstrip("/")          # https://ryoko-funamoto.jp
WP_USER     = os.environ["WP_USER"]
WP_APP_PASS = os.environ["WP_APP_PASS"]

API_BASE    = f"{WP_URL}/wp-json/wp/v2"
AUTH        = (WP_USER, WP_APP_PASS)

# ── カテゴリマッピング（WP taxonomy: event_category のスラッグ） ──
CATEGORY_MAP = {
    "市のイベント":     ["市", "市主催", "市役所", "行政", "公式"],
    "市民主催":         ["町内", "地域", "住民", "市民", "NPO", "ボランティア"],
    "講座・研修":       ["講座", "研修", "ワークショップ", "教室", "習い"],
    "講演・シンポ":     ["講演", "シンポジウム", "フォーラム", "トーク", "講話"],
    "マルシェ・市場":   ["マルシェ", "朝市", "直売", "マーケット", "フリマ", "フリーマーケット"],
    "スポーツ・健康":   ["スポーツ", "健康", "体操", "ランニング", "登山", "ハイキング", "水泳"],
    "お祭り・伝統行事": ["祭り", "まつり", "神社", "神輿", "伝統", "盆踊り", "花火"],
}

def guess_category(title: str, desc: str = "") -> str:
    text = title + " " + desc
    for cat, keywords in CATEGORY_MAP.items():
        for kw in keywords:
            if kw in text:
                return cat
    return "その他"

def make_dedup_key(title: str, date: str) -> str:
    return hashlib.md5(f"{date}|{title}".encode()).hexdigest()[:12]


# ── WordPress ヘルパー ────────────────────────────────────────────

def get_existing_dedup_keys() -> set:
    """公開済み・下書きを問わず既存イベントのキーを収集（重複防止）"""
    keys = set()
    page = 1
    while True:
        r = requests.get(
            f"{API_BASE}/shiso_event",
            params={"per_page": 100, "page": page, "status": "any"},
            auth=AUTH, timeout=20
        )
        if r.status_code == 400:
            break
        r.raise_for_status()
        items = r.json()
        if not items:
            break
        for item in items:
            # タイトルと日付からキーを再生成して照合
            title = item.get("title", {}).get("rendered", "")
            date  = item.get("meta", {}).get("_event_date", "")[:10]
            keys.add(make_dedup_key(title, date))
        page += 1
    return keys

def get_or_create_term(name: str) -> int:
    """event_category タクソノミーのターム ID を取得（なければ作成）"""
    r = requests.get(
        f"{API_BASE}/event_category",
        params={"search": name, "per_page": 5},
        auth=AUTH, timeout=20
    )
    r.raise_for_status()
    results = r.json()
    for t in results:
        if t["name"] == name:
            return t["id"]
    # 新規作成
    cr = requests.post(
        f"{API_BASE}/event_category",
        json={"name": name},
        auth=AUTH, timeout=20
    )
    cr.raise_for_status()
    return cr.json()["id"]

def post_event(ev: dict, term_id: int) -> dict:
    payload = {
        "title":          ev["title"],
        "content":        ev.get("description", ""),
        "status":         "draft",
        "event_category": [term_id],
        "meta": {
            "_event_date":     ev.get("date", ""),
            "_event_location": ev.get("location", ""),
            "_event_url":      ev.get("url", ""),
        },
    }
    r = requests.post(f"{API_BASE}/shiso_event", json=payload, auth=AUTH, timeout=30)
    r.raise_for_status()
    return r.json()


# ── スクレイパー ─────────────────────────────────────────────────

SOURCES = [
    {
        "name": "宍粟市公式イベント",
        "url":  "https://www.shiso.lg.jp/category/event/",
    },
]

def scrape_shiso_official(url: str) -> list:
    resp = requests.get(url, timeout=30, headers={
        "User-Agent": "Mozilla/5.0 (compatible; ShisoEventBot/1.0)"
    })
    resp.encoding = resp.apparent_encoding or "utf-8"
    soup = BeautifulSoup(resp.text, "lxml")
    events = []

    articles = soup.select("article, .post, .entry, li.event-item, .news-item")

    for article in articles:
        try:
            title_el = article.select_one("h2, h3, h4, .entry-title, .post-title")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            if not title:
                continue

            # 日付
            date_str = ""
            date_el = article.select_one("time, .date, .post-date, .entry-date")
            if date_el:
                dt = date_el.get("datetime", "")
                if dt:
                    date_str = dt[:10]
                else:
                    raw = date_el.get_text(strip=True)
                    m = re.search(r"(\d{4})[年\-/.](\d{1,2})[月\-/.](\d{1,2})", raw)
                    if m:
                        date_str = f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"

            # 場所
            location = ""
            loc_el = article.select_one(".location, .venue, [class*='place']")
            if loc_el:
                location = loc_el.get_text(strip=True)

            # 本文抜粋
            desc_el = article.select_one(".entry-content, .excerpt, p")
            desc = desc_el.get_text(strip=True)[:300] if desc_el else ""

            # リンク
            link_el = title_el.find("a") or article.select_one("a[href]")
            article_url = ""
            if link_el:
                href = link_el.get("href", "")
                if href.startswith("http"):
                    article_url = href
                elif href.startswith("/"):
                    article_url = "https://www.shiso.lg.jp" + href

            events.append({
                "title":       title,
                "date":        date_str,
                "location":    location,
                "description": desc,
                "url":         article_url,
                "category":    guess_category(title, desc),
            })
        except Exception as e:
            print(f"  行スキップ: {e}", file=sys.stderr)

    return events


# ── メイン ──────────────────────────────────────────────────────

def main():
    print(f"[{datetime.now(JST).strftime('%Y-%m-%d %H:%M')} JST] イベント収集開始")

    # 重複チェック用キーを取得
    print("既存イベントを確認中...")
    try:
        existing_keys = get_existing_dedup_keys()
        print(f"  既存: {len(existing_keys)} 件")
    except Exception as e:
        print(f"  既存イベント取得エラー: {e}", file=sys.stderr)
        existing_keys = set()

    # ターム ID キャッシュ
    term_cache: dict[str, int] = {}

    total_new = 0

    for src in SOURCES:
        print(f"\n収集中: {src['url']}")
        try:
            events = scrape_shiso_official(src["url"])
            print(f"  取得: {len(events)} 件")
        except Exception as e:
            print(f"  スクレイピングエラー: {e}", file=sys.stderr)
            continue

        for ev in events:
            key = make_dedup_key(ev["title"], ev["date"])
            if key in existing_keys:
                continue  # 重複スキップ

            cat_name = ev["category"]
            if cat_name not in term_cache:
                try:
                    term_cache[cat_name] = get_or_create_term(cat_name)
                except Exception as e:
                    print(f"  カテゴリ作成エラー ({cat_name}): {e}", file=sys.stderr)
                    term_cache[cat_name] = 0

            try:
                result = post_event(ev, term_cache[cat_name])
                print(f"  ✅ 下書き追加: {ev['title']} ({ev['date']}) → WP ID {result['id']}")
                existing_keys.add(key)
                total_new += 1
            except Exception as e:
                print(f"  ❌ 投稿エラー ({ev['title']}): {e}", file=sys.stderr)

    print(f"\n完了: {total_new} 件を下書きで追加しました")
    if total_new > 0:
        print(f"確認: {WP_URL}/wp-admin/edit.php?post_type=shiso_event&post_status=draft")


if __name__ == "__main__":
    main()
