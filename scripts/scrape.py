#!/usr/bin/env python3
import argparse
import json
import time
from datetime import datetime
from typing import List, Dict, Optional, Set
from playwright.sync_api import sync_playwright

def extract_posts_from_articles(articles) -> List[Dict]:
    posts = []
    for a in articles:
        try:
            # 投稿ID / permalink を見つける
            link_el = None
            anchors = a.query_selector_all("a")
            status_href = None
            for an in anchors:
                href = an.get_attribute("href") or ""
                if "/status/" in href:
                    status_href = href
                    link_el = an
                    break
            if status_href:
                status_id = status_href.rstrip("/").split("/")[-1]
                permalink = "https://x.com" + status_href
            else:
                # 一意識別子が取れない場合はスキップ
                continue

            # 時刻
            time_el = a.query_selector("time")
            timestamp = None
            if time_el:
                dt = time_el.get_attribute("datetime")
                if dt:
                    timestamp = dt

            # コンテンツ（言語付きテキスト要素）
            content_el = a.query_selector('div[lang]')
            text = content_el.inner_text().strip() if content_el else a.inner_text().strip()

            # 画像（除外条件でアバター等を省く簡易フィルタ）
            img_els = a.query_selector_all("img")
            imgs = []
            for img in img_els:
                src = img.get_attribute("src") or ""
                alt = img.get_attribute("alt") or ""
                # avatar を除外する簡易判定
                if "profile_images" in src or "avatar" in alt.lower() or "avatar" in src:
                    continue
                if src and src not in imgs:
                    imgs.append(src)

            posts.append({
                "id": status_id,
                "permalink": permalink,
                "timestamp": timestamp,
                "text": text,
                "images": imgs,
            })
        except Exception as e:
            # 要素解析で失敗しても続行
            print("Warning: failed to parse an article:", e)
            continue
    return posts

def scrape(username: str, max_posts: int = 500, max_scrolls: int = 60, scroll_pause: float = 1.0, headless: bool = True, timeout_s: int = 60):
    url = f"https://x.com/{username}"
    print(f"Start scraping {url} (max_posts={max_posts}, max_scrolls={max_scrolls})")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(java_script_enabled=True, user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Playwright")
        page = context.new_page()
        page.set_default_navigation_timeout(timeout_s * 1000)
        page.goto(url, wait_until="networkidle")
        time.sleep(1.0)

        collected: Dict[str, Dict] = {}
        prev_count = 0
        scrolls = 0
        idle_rounds = 0

        while len(collected) < max_posts and scrolls < max_scrolls and idle_rounds < 5:
            # Collect current articles
            articles = page.query_selector_all("article")
            new_posts = extract_posts_from_articles(articles)
            for pitem in new_posts:
                pid = pitem.get("id")
                if not pid:
                    continue
                # keep newest (if duplicates, overwrite)
                collected[pid] = pitem

            current_count = len(collected)
            print(f"Scroll {scrolls}: found {len(articles)} article elements, unique posts collected={current_count}")

            # If no new posts since last iteration, increase idle counter
            if current_count == prev_count:
                idle_rounds += 1
            else:
                idle_rounds = 0
                prev_count = current_count

            if len(collected) >= max_posts:
                break

            # Scroll to bottom to trigger lazy load
            page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            time.sleep(scroll_pause)
            scrolls += 1

        # Convert to list and sort by timestamp (newest first). If timestamp missing, keep as-is.
        posts_list = list(collected.values())

        def sort_key(item):
            ts = item.get("timestamp")
            if not ts:
                return datetime.min
            try:
                return datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                return datetime.min

        posts_list.sort(key=sort_key, reverse=True)

        # Trim to max_posts
        posts_list = posts_list[:max_posts]

        # Save to posts.json
        with open("posts.json", "w", encoding="utf-8") as f:
            json.dump({
                "scraped_at": datetime.utcnow().isoformat() + "Z",
                "target": username,
                "count": len(posts_list),
                "posts": posts_list
            }, f, ensure_ascii=False, indent=2)

        print(f"Finished: saved {len(posts_list)} posts to posts.json")
        browser.close()

def main():
    parser = argparse.ArgumentParser(description="Scrape public X profile posts using Playwright.")
    parser.add_argument("--username", "-u", required=True, help="X username (without @). e.g. luchia")
    parser.add_argument("--max-posts", type=int, default=500, help="Maximum number of posts to collect")
    parser.add_argument("--max-scrolls", type=int, default=60, help="Maximum scroll attempts")
    parser.add_argument("--scroll-pause", type=float, default=1.0, help="Seconds to wait after each scroll")
    parser.add_argument("--headless", type=lambda s: s.lower() in ("1", "true", "yes"), default=True, help="Run browser headless")
    args = parser.parse_args()

    scrape(
        username=args.username,
        max_posts=args.max_posts,
        max_scrolls=args.max_scrolls,
        scroll_pause=args.scroll_pause,
        headless=args.headless
    )

if __name__ == "__main__":
    main()
