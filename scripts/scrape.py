#!/usr/bin/env python3
import argparse
import json
import time
import sys
from datetime import datetime
from typing import List, Dict
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

def extract_posts_from_articles(articles) -> List[Dict]:
    posts = []
    for a in articles:
        try:
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
                continue

            time_el = a.query_selector("time")
            timestamp = None
            if time_el:
                dt = time_el.get_attribute("datetime")
                if dt:
                    timestamp = dt

            content_el = a.query_selector('div[lang]')
            text = content_el.inner_text().strip() if content_el else a.inner_text().strip()

            img_els = a.query_selector_all("img")
            imgs = []
            for img in img_els:
                src = img.get_attribute("src") or ""
                alt = img.get_attribute("alt") or ""
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
            print("Warning: failed to parse an article:", e)
            continue
    return posts

def navigate_with_retries(page, url: str, timeout_s: int, max_attempts: int = 3) -> bool:
    """
    Try to navigate to url with networkidle. On timeout, fallback to domcontentloaded
    and wait for an 'article' element. Retry a few times with backoff.
    Returns True if navigation succeeded enough to continue scraping.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            print(f"Navigating to {url} (attempt {attempt}/{max_attempts}) - wait_until=networkidle")
            page.goto(url, timeout=timeout_s * 1000, wait_until="networkidle")
            return True
        except PlaywrightTimeoutError:
            print(f"Warning: networkidle timeout on attempt {attempt}/{max_attempts}")
            # try fallback
            try:
                print(f"Fallback: goto with domcontentloaded and wait for 'article' (attempt {attempt})")
                page.goto(url, timeout=timeout_s * 1000, wait_until="domcontentloaded")
                # wait for at least one article to appear (shorter timeout)
                page.wait_for_selector("article", timeout=10_000)
                return True
            except PlaywrightTimeoutError:
                print(f"Warning: domcontentloaded/article wait failed on attempt {attempt}")
                # small backoff before retrying
                time.sleep(2 * attempt)
                continue
            except Exception as e:
                print(f"Warning: unexpected error during fallback navigation: {e}")
                time.sleep(2 * attempt)
                continue
        except Exception as e:
            print(f"Warning: unexpected navigation error: {e}")
            time.sleep(2 * attempt)
            continue
    return False

def scrape(username: str, max_posts: int = 500, max_scrolls: int = 60, scroll_pause: float = 1.0, headless: bool = True, timeout_s: int = 120):
    url = f"https://x.com/{username}"
    print(f"Start scraping {url} (max_posts={max_posts}, max_scrolls={max_scrolls}, timeout_s={timeout_s})")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            java_script_enabled=True,
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Playwright",
            viewport={"width": 1280, "height": 720}
        )
        page = context.new_page()
        page.set_default_navigation_timeout(timeout_s * 1000)

        ok = navigate_with_retries(page, url, timeout_s=timeout_s, max_attempts=3)
        if not ok:
            print("Error: Failed to load page after retries. Attempting to continue with current content (if any).")
            # If navigation completely failed, attempt to continue but warn user
            # Optionally exit with error: here we continue and will likely collect nothing.
        else:
            # give a short pause for any lazy JS
            time.sleep(1.0)

        collected: Dict[str, Dict] = {}
        prev_count = 0
        scrolls = 0
        idle_rounds = 0

        while len(collected) < max_posts and scrolls < max_scrolls and idle_rounds < 5:
            try:
                articles = page.query_selector_all("article")
            except Exception as e:
                print("Warning: failed to query article elements:", e)
                articles = []

            new_posts = extract_posts_from_articles(articles)
            for pitem in new_posts:
                pid = pitem.get("id")
                if not pid:
                    continue
                collected[pid] = pitem

            current_count = len(collected)
            print(f"Scroll {scrolls}: found {len(articles)} article elements, unique posts collected={current_count}")

            if current_count == prev_count:
                idle_rounds += 1
            else:
                idle_rounds = 0
                prev_count = current_count

            if len(collected) >= max_posts:
                break

            try:
                page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            except Exception as e:
                print("Warning: scroll evaluate failed:", e)

            time.sleep(scroll_pause)
            scrolls += 1

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
        posts_list = posts_list[:max_posts]

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
    parser.add_argument("--timeout", type=int, default=120, help="Navigation timeout in seconds (default 120)")
    args = parser.parse_args()

    try:
        scrape(
            username=args.username,
            max_posts=args.max_posts,
            max_scrolls=args.max_scrolls,
            scroll_pause=args.scroll_pause,
            headless=args.headless,
            timeout_s=args.timeout
        )
    except Exception as e:
        print("Error: uncaught exception in scraper:", e, file=sys.stderr)
        raise

if __name__ == "__main__":
    main()
