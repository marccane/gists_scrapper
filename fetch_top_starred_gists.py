import argparse
import json
import os
import re
import time
from datetime import datetime
from urllib.parse import urlparse

import requests
from lxml import html


# Fill this if you do not want to use the GITHUB_TOKEN env var.
GITHUB_TOKEN = ""


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Read GitHub profile URLs from a file, fetch each user's gists, "
            "and print top-N gists sorted by stars."
        )
    )
    parser.add_argument(
        "--input-file",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "hrefs_new_sorted.txt"),
        help="Path to the file containing GitHub profile URLs (one per line).",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=3,
        help="How many top gists to print per user.",
    )
    parser.add_argument(
        "--max-users",
        type=int,
        default=0,
        help="Limit number of users to process (0 = all users).",
    )
    parser.add_argument(
        "--max-gists-per-user",
        type=int,
        default=30,
        help="Limit number of gists fetched per user for star parsing.",
    )
    parser.add_argument(
        "--sleep-ms",
        type=int,
        default=120,
        help="Sleep time between HTTP requests in milliseconds.",
    )
    parser.add_argument(
        "--max-pages-per-user",
        type=int,
        default=20,
        help="Max gist-list pages to fetch per user.",
    )
    parser.add_argument(
        "--cache-file",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "gists_cache.json"),
        help="Path to local cache JSON file.",
    )
    parser.add_argument(
        "--refresh-user-gists",
        action="store_true",
        help="Ignore cached user gist lists and refetch from API.",
    )
    parser.add_argument(
        "--refresh-gist-social",
        action="store_true",
        help="Ignore cached gist stars/forks and refetch gist pages.",
    )
    parser.add_argument(
        "--verbose-cache",
        action="store_true",
        help="Print cache hit/miss information.",
    )
    return parser.parse_args()


def build_session():
    session = requests.Session()
    token = os.getenv("GITHUB_TOKEN", "").strip() or GITHUB_TOKEN.strip()
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "gist-star-scan-script"}
    if token and token != "YOUR_GITHUB_ACCESS_TOKEN":
        headers["Authorization"] = f"Bearer {token}"
    session.headers.update(headers)
    return session


def extract_username(profile_url):
    parsed = urlparse(profile_url.strip())
    if parsed.netloc != "github.com":
        return None
    path_parts = [p for p in parsed.path.split("/") if p]
    if not path_parts:
        return None
    return path_parts[0]


def parse_compact_number(text):
    # Converts text like:
    # - "1.2k" -> 1200
    # - "987" -> 987
    # - "10 stars" -> 10
    # - "2 forks" -> 2
    cleaned = text.strip().lower().replace(",", "")
    if not cleaned:
        return 0
    match = re.search(r"(\d+(?:\.\d+)?)([km]?)", cleaned)
    if not match:
        return 0
    number_text, suffix = match.groups()
    multiplier = 1
    if suffix == "k":
        multiplier = 1000
    elif suffix == "m":
        multiplier = 1000000
    try:
        return int(float(number_text) * multiplier)
    except ValueError:
        return 0


def extract_social_count(gist_html, marker):
    # Looks for a social-count link around marker (e.g., "stargazers" or "network/members").
    pattern = rf'href="[^"]*{re.escape(marker)}[^"]*"[^>]*>\s*([^<]+)\s*</a>'
    match = re.search(pattern, gist_html, flags=re.IGNORECASE)
    if not match:
        return 0
    return parse_compact_number(match.group(1))


def fetch_user_gists(session, username):
    api_url = f"https://api.github.com/users/{username}/gists"
    response = session.get(api_url, params={"per_page": 100}, timeout=30)
    if response.status_code == 404:
        return []
    response.raise_for_status()
    return response.json()


def gist_id_from_url(gist_url):
    parsed = urlparse(gist_url)
    path_parts = [p for p in parsed.path.split("/") if p]
    if not path_parts:
        return None
    return path_parts[-1]


def fetch_user_gist_list_stats(session, username, max_pages, sleep_ms):
    stats_by_gist_id = {}
    base_url = f"https://gist.github.com/{username}"

    for page in range(1, max_pages + 1):
        page_url = f"{base_url}?page={page}"
        response = session.get(page_url, timeout=30)
        if response.status_code == 404:
            break
        response.raise_for_status()

        tree = html.fromstring(response.text)
        snippets = tree.xpath("//div[contains(@class,'gist-snippet')]")
        if not snippets:
            break

        found_on_page = 0
        for snippet in snippets:
            hrefs = snippet.xpath(".//a[contains(@href,'/stargazers')]/@href")
            if not hrefs:
                hrefs = snippet.xpath(".//a[contains(@href,'/network/members')]/@href")
            if not hrefs:
                hrefs = snippet.xpath(".//a[contains(@href,'/comments')]/@href")
            if not hrefs:
                hrefs = snippet.xpath(".//a[contains(@href,'/raw/')]/@href")

            gist_id = None
            for href in hrefs:
                gist_id = gist_id_from_url(f"https://gist.github.com{href}")
                if gist_id:
                    break
            if not gist_id:
                continue

            stars_text = "".join(
                snippet.xpath(".//a[contains(@href,'/stargazers')]/text()")
            ).strip()
            forks_text = "".join(
                snippet.xpath(".//a[contains(@href,'/network/members')]/text()")
            ).strip()
            comments_text = "".join(
                snippet.xpath(".//a[contains(@href,'/comments')]/text()")
            ).strip()

            stats_by_gist_id[gist_id] = {
                "stars": parse_compact_number(stars_text),
                "forks": parse_compact_number(forks_text),
                "comments": parse_compact_number(comments_text),
            }
            found_on_page += 1

        if found_on_page == 0:
            break

        time.sleep(max(sleep_ms, 0) / 1000.0)

    return stats_by_gist_id


def format_date(iso_date):
    if not iso_date:
        return "n/a"
    try:
        return datetime.fromisoformat(iso_date.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except ValueError:
        return iso_date


def read_profile_urls(path):
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def load_cache(path):
    if not os.path.exists(path):
        return {"users": {}, "gists": {}, "user_list_stats": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"users": {}, "gists": {}, "user_list_stats": {}}
        data.setdefault("users", {})
        data.setdefault("gists", {})
        data.setdefault("user_list_stats", {})
        return data
    except (OSError, json.JSONDecodeError):
        return {"users": {}, "gists": {}, "user_list_stats": {}}


def save_cache(path, cache):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=True, indent=2, sort_keys=True)
    except OSError as exc:
        print(f"Warning: failed to write cache file {path}: {exc}")


def get_user_gists_cached(session, username, cache, force_refresh=False):
    if not force_refresh and username in cache["users"]:
        return cache["users"][username], True
    gists = fetch_user_gists(session, username)
    cache["users"][username] = gists
    return gists, False


def get_user_list_stats_cached(
    session, username, cache, max_pages, sleep_ms, force_refresh=False
):
    if not force_refresh and username in cache["user_list_stats"]:
        return cache["user_list_stats"][username], True
    stats_by_gist_id = fetch_user_gist_list_stats(
        session, username, max_pages=max_pages, sleep_ms=sleep_ms
    )
    cache["user_list_stats"][username] = stats_by_gist_id
    return stats_by_gist_id, False


def main():
    args = parse_args()
    session = build_session()
    cache = load_cache(args.cache_file)
    profile_urls = read_profile_urls(args.input_file)

    if args.max_users > 0:
        profile_urls = profile_urls[: args.max_users]

    if not profile_urls:
        print("No profile URLs found.")
        return

    print(f"Processing {len(profile_urls)} user(s) from {args.input_file}\n")
    print(f"Using cache file: {args.cache_file}\n")

    total_user_cache_hits = 0
    total_user_cache_misses = 0
    total_gist_cache_hits = 0
    total_gist_cache_misses = 0

    for profile_url in profile_urls:
        username = extract_username(profile_url)
        if not username:
            print(f"Skipping invalid GitHub profile URL: {profile_url}")
            continue

        try:
            gists, user_cache_hit = get_user_gists_cached(
                session,
                username,
                cache,
                force_refresh=args.refresh_user_gists,
            )
            if user_cache_hit:
                total_user_cache_hits += 1
            else:
                total_user_cache_misses += 1
            if args.verbose_cache:
                print(
                    f"[{username}] user gists cache: "
                    f"{'HIT' if user_cache_hit else 'MISS'}"
                )
        except requests.RequestException as exc:
            print(f"[{username}] Failed to fetch gist list: {exc}")
            print("---")
            save_cache(args.cache_file, cache)
            continue

        if not gists:
            print(f"[{username}] No gists found. (user cache: {'HIT' if user_cache_hit else 'MISS'})")
            print("---")
            save_cache(args.cache_file, cache)
            continue

        try:
            list_stats_by_gist_id, list_stats_cache_hit = get_user_list_stats_cached(
                session,
                username,
                cache,
                max_pages=args.max_pages_per_user,
                sleep_ms=args.sleep_ms,
                force_refresh=args.refresh_gist_social,
            )
        except requests.RequestException as exc:
            print(f"[{username}] Failed to fetch gist list page stats: {exc}")
            print("---")
            save_cache(args.cache_file, cache)
            continue

        if args.verbose_cache:
            print(
                f"[{username}] gist list stats cache: "
                f"{'HIT' if list_stats_cache_hit else 'MISS'}"
            )

        enriched = []
        user_gist_hits = 0
        user_gist_misses = 0
        for gist in gists[: args.max_gists_per_user]:
            gist_url = gist.get("html_url")
            if not gist_url:
                continue
            gist_id = gist.get("id") or gist_id_from_url(gist_url)
            cached_social = None
            if not args.refresh_gist_social:
                cached_social = cache["gists"].get(gist_id) if gist_id else None
                if cached_social is None and gist_url in cache["gists"]:
                    cached_social = cache["gists"].get(gist_url)
            if cached_social is not None:
                stars = int(cached_social.get("stars", 0))
                forks = int(cached_social.get("forks", 0))
                gist_comments = int(cached_social.get("comments", gist.get("comments", 0)))
                gist_cache_hit = True
            else:
                listed = list_stats_by_gist_id.get(gist_id or "")
                if listed is None:
                    stars, forks = 0, 0
                    gist_comments = int(gist.get("comments", 0))
                    gist_cache_hit = False
                else:
                    stars = int(listed.get("stars", 0))
                    forks = int(listed.get("forks", 0))
                    gist_comments = int(listed.get("comments", gist.get("comments", 0)))
                    gist_cache_hit = False

                cache_key = gist_id or gist_url
                cache["gists"][cache_key] = {
                    "stars": stars,
                    "forks": forks,
                    "comments": gist_comments,
                }

            if gist_cache_hit:
                user_gist_hits += 1
                total_gist_cache_hits += 1
            else:
                user_gist_misses += 1
                total_gist_cache_misses += 1

            enriched.append(
                {
                    "description": gist.get("description") or "No description",
                    "url": gist_url,
                    "stars": stars,
                    "forks": forks,
                    "comments": gist_comments,
                    "files": len(gist.get("files", {})),
                    "updated_at": gist.get("updated_at", ""),
                }
            )

        if not enriched:
            print(f"[{username}] Could not fetch any gist stats.")
            print("---")
            save_cache(args.cache_file, cache)
            continue

        top = sorted(
            enriched,
            key=lambda item: (item["stars"], item["forks"], item["comments"]),
            reverse=True,
        )[: args.top_n]

        print(f"[{username}] Top {len(top)} gist(s) by stars")
        for idx, gist in enumerate(top, start=1):
            print(f"{idx}. {gist['description']}")
            print(f"   URL: {gist['url']}")
            print(
                f"   Stars: {gist['stars']} | Forks: {gist['forks']} | "
                f"Comments: {gist['comments']} | Files: {gist['files']} | "
                f"Updated: {format_date(gist['updated_at'])}"
            )
        print(
            f"   Cache summary: user_gists={'HIT' if user_cache_hit else 'MISS'} | "
            f"gist_stats hits={user_gist_hits}, misses={user_gist_misses}"
        )
        print("---")
        # Save incrementally so interrupted runs still keep progress.
        save_cache(args.cache_file, cache)

    save_cache(args.cache_file, cache)
    print(
        "\nOverall cache summary: "
        f"user_gists hits={total_user_cache_hits}, misses={total_user_cache_misses} | "
        f"gist_stats hits={total_gist_cache_hits}, misses={total_gist_cache_misses}"
    )


if __name__ == "__main__":
    main()
