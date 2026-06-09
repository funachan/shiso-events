#!/usr/bin/env python3
"""
宍粟市イベント情報を収集し、WordPress REST API 経由で下書き投稿するスクリプト。
"""

import hashlib
import os
import re
import sys
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup

JST = timezone(timedelta(hours=9))

WP_URL      = os.environ["WP_URL"].rstrip("/")
WP_USER     = os.environ["WP_USER"]
WP_APP_PASS = os.environ["WP_APP_PASS"]
API_BASE    = f"{WP_URL}/wp-json/wp/v2"
AUTH        = (WP_USER, WP_APP_PASS)

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ShisoEventBot/1.0)"}

# ── カテゴリキーワードマッピング ─────────────────────────────────
CATEGORY_MAP = {
    "市のイベント":     ["市主催", "市役所", "行政", "宍粟市主催", "公式"],
    "市民主催":         ["町内", "地域", "住民", "市民", "NPO", "ボランティア", "地区"],
    "講座・研修":       ["講座", "研修", "ワークショップ", "教室", "習い", "体験"],
    "講演・シンポ":     ["講演", "シンポジウム", "フォーラム", "トーク", "講話", "記念式"],
    "マルシェ・市場":   ["マルシェ", "朝市", "直売", "マーケット", "フリマ", "バザー"],
    "スポーツ・健康":   ["スポーツ", "健康", "体操", "ランニング", "登山", "ハイキング", "水泳", "マラソン"],
    "お祭り・伝統行事": ["祭り", "まつり", "神社", "神輿", "伝統", "盆踊り", "花火", "秋祭"],
}

def guess_category(title: str, desc: str = "") -> str:
    text = title + " " + desc
    for cat, keywords in CATEGORY_MAP.items():
        for kw in keywords:
            if kw in text:
                return cat
    return "市のイベント"  # デフォルトは市のイベント

def make_key(title: str, date: str) -> str:
    return hashlib.md5(f"{date}|{title}".encode()).hexdigest()[:12]


# ── WordPress ヘルパー ────────────────────────────────────────────

def get_existing_keys() -> set:
    """既存イベントのキーを取得（重複防止）"""
    keys = set()
    page = 1
    while True:
        try:
            r = requests.get(
                f"{API_BASE}/shiso_event",
                params={"per_page": 100, "page": page, "status": "publish,draft"},
                auth=AUTH, timeout=20
            )
            if r.status_code in (400, 403):
                # アクセス制限の場合はスキップして続行
                print(f"  既存チェックスキップ (status {r.status_code}) — 重複確認なしで続行", file=sys.stderr)
                break
            r.raise_for_status()
            items = r.json()
            if not items:
                break
            for item in items:
                title = item.get("title", {}).get("rendered", "")
                date  = (item.get("meta") or {}).get("event_date", "")[:10]
                keys.add(make_key(title, date))
            page += 1
        except Exception as e:
            print(f"  既存チェックエラー: {e}", file=sys.stderr)
            break
    return keys

def get_or_create_term(name: str) -> int:
    r = requests.get(f"{API_BASE}/event_category",
                     params={"search": name, "per_page": 5},
                     auth=AUTH, timeout=20)
    r.raise_for_status()
    for t in r.json():
        if t["name"] == name:
            return t["id"]
    cr = requests.post(f"{API_BASE}/event_category",
                       json={"name": name}, auth=AUTH, timeout=20)
    cr.raise_for_status()
    return cr.json()["id"]

def post_event(ev: dict, term_id: int) -> dict:
    payload = {
        "title":          ev["title"],
        "content":        ev.get("description", ""),
        "status":         "draft",
        "event_category": [term_id],
        "event_date":     ev.get("date", ""),
        "event_location": ev.get("location", ""),
        "event_url":      ev.get("url", ""),
    }
    r = requests.post(f"{API_BASE}/shiso_event", json=payload, auth=AUTH, timeout=30)
    r.raise_for_status()
    return r.json()


# ── スクレイパー ─────────────────────────────────────────────────

def parse_date_text(text: str) -> str:
    """日本語テキストから YYYY-MM-DD を抽出"""
    m = re.search(r"(\d{4})[年\-/.](\d{1,2})[月\-/.](\d{1,2})", text)
    if m:
        return f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"
    # R7形式（令和）
    m2 = re.search(r"令和(\d+)年(\d{1,2})月(\d{1,2})日", text)
    if m2:
        year = 2018 + int(m2.group(1))
        return f"{year}-{m2.group(2).zfill(2)}-{m2.group(3).zfill(2)}"
    return ""

def scrape_city_shiso(url: str, base: str = "https://www.city.shiso.lg.jp") -> list:
    """宍粟市公式カレンダー https://www.city.shiso.lg.jp/calendar.html
    構造: 週グリッド形式（日〜土の7列）、各セルに日付番号＋イベントリンク
    """
    resp = requests.get(url, timeout=30, headers=HEADERS)
    resp.encoding = resp.apparent_encoding or "utf-8"
    soup = BeautifulSoup(resp.text, "lxml")
    events = []

    # ページ全体のテキストから年月を取得
    page_year  = datetime.now(JST).year
    page_month = datetime.now(JST).month
    full_text = soup.get_text()
    m = re.search(r"令和(\d+)年(\d+)月", full_text)
    if m:
        page_year  = 2018 + int(m.group(1))
        page_month = int(m.group(2))
    else:
        m2 = re.search(r"(\d{4})年(\d+)月", full_text)
        if m2:
            page_year  = int(m2.group(1))
            page_month = int(m2.group(2))
    print(f"  対象年月: {page_year}年{page_month}月")

    seen = set()

    # 構造: 各 <tr> が1日分
    # <th>日付番号</th> <td class="day">曜日</td> <td>イベントリンク</td>
    for tr in soup.select("table tr"):
        try:
            # 日付番号を <th> から取得
            th = tr.find("th")
            if not th:
                continue
            day_text = th.get_text(strip=True)
            if not day_text.isdigit():
                continue
            day = int(day_text)
            if not 1 <= day <= 31:
                continue
            date_str = f"{page_year}-{str(page_month).zfill(2)}-{str(day).zfill(2)}"

            # イベント列（class のない td）からリンクを取得
            for td in tr.find_all("td"):
                if td.get("class"):
                    continue  # class="day" の曜日列はスキップ
                links = td.find_all("a")
                if links:
                    for a in links:
                        title = a.get_text(strip=True)
                        if not title or len(title) < 3:
                            continue
                        key = f"{date_str}|{title}"
                        if key in seen:
                            continue
                        seen.add(key)
                        href = a.get("href", "")
                        ev_url = href if href.startswith("http") else base + href
                        events.append({
                            "title":       title,
                            "date":        date_str,
                            "location":    "",
                            "description": "",
                            "url":         ev_url,
                            "category":    guess_category(title),
                        })
                else:
                    # リンクなしのテキストイベント
                    text = td.get_text(strip=True)
                    if text and len(text) >= 4:
                        key = f"{date_str}|{text}"
                        if key not in seen:
                            seen.add(key)
                            events.append({
                                "title":       text,
                                "date":        date_str,
                                "location":    "",
                                "description": "",
                                "url":         "",
                                "category":    guess_category(text),
                            })
        except Exception as e:
            print(f"  行スキップ: {e}", file=sys.stderr)

    return events

def scrape_generic(url: str, base_url: str) -> list:
    """汎用スクレイパー（市民団体・観光サイト等）"""
    resp = requests.get(url, timeout=30, headers=HEADERS)
    resp.encoding = resp.apparent_encoding or "utf-8"
    soup = BeautifulSoup(resp.text, "lxml")
    events = []

    for article in soup.select("article, .post, .entry, li.item, .news-item"):
        try:
            title_el = article.select_one("h2,h3,h4,.title,a")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            if not title or len(title) < 3:
                continue
            date_str = parse_date_text(article.get_text())
            link_el  = article.select_one("a[href]")
            ev_url   = ""
            if link_el:
                href = link_el.get("href", "")
                ev_url = href if href.startswith("http") else base_url + href
            desc_el = article.select_one("p,.excerpt")
            desc = desc_el.get_text(strip=True)[:200] if desc_el else ""
            events.append({
                "title": title, "date": date_str,
                "location": "", "description": desc,
                "url": ev_url, "category": guess_category(title, desc),
            })
        except Exception:
            pass
    return events


SOURCES = [
    {
        "name":    "宍粟市公式カレンダー",
        "url":     "https://www.city.shiso.lg.jp/calendar.html",
        "scraper": scrape_city_shiso,
        "base":    "https://www.city.shiso.lg.jp",
    },
    {
        "name":    "宍粟市観光ナビ",
        "url":     "https://shiso-navi.jp/event/",
        "scraper": scrape_generic,
        "base":    "https://shiso-navi.jp",
    },
]


# ── メイン ──────────────────────────────────────────────────────

def main():
    print(f"[{datetime.now(JST).strftime('%Y-%m-%d %H:%M')} JST] イベント収集開始")

    print("既存イベントを確認中...")
    existing_keys = get_existing_keys()
    print(f"  既存: {len(existing_keys)} 件")

    term_cache: dict[str, int] = {}
    total_new = 0

    for src in SOURCES:
        print(f"\n収集中: {src['url']}")
        try:
            events = src["scraper"](src["url"], src.get("base", ""))
            print(f"  取得: {len(events)} 件")
        except Exception as e:
            print(f"  スクレイピングエラー: {e}", file=sys.stderr)
            continue

        for ev in events:
            if not ev["title"]:
                continue
            key = make_key(ev["title"], ev["date"])
            if key in existing_keys:
                continue

            cat_name = ev["category"]
            if cat_name not in term_cache:
                try:
                    term_cache[cat_name] = get_or_create_term(cat_name)
                except Exception as e:
                    print(f"  カテゴリエラー ({cat_name}): {e}", file=sys.stderr)
                    term_cache[cat_name] = 0

            try:
                result = post_event(ev, term_cache[cat_name])
                print(f"  ✅ 下書き追加: 「{ev['title']}」({ev['date']}) → WP ID {result['id']}")
                existing_keys.add(key)
                total_new += 1
            except Exception as e:
                body = getattr(e.response, 'text', '') if hasattr(e, 'response') else str(e)
                print(f"  ❌ 投稿エラー ({ev['title']}): {e} | {body[:200]}", file=sys.stderr)

    print(f"\n完了: {total_new} 件を下書きで追加しました")
    if total_new > 0:
        print(f"確認: {WP_URL}/wp-admin/edit.php?post_type=shiso_event&post_status=draft")


if __name__ == "__main__":
    main()
