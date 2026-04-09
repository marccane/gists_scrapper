import argparse
import json
import os
import time
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import requests


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
        help="Limit number of gists processed per user.",
    )
    parser.add_argument(
        "--sleep-ms",
        type=int,
        default=120,
        help="Sleep time between HTTP requests in milliseconds.",
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
        help="Ignore cached gist metrics and refetch from API.",
    )
    parser.add_argument(
        "--include-starred-status",
        action="store_true",
        help=(
            "Check if each gist is starred by the authenticated user via "
            "GET /gists/{gist_id}/star (extra API requests)."
        ),
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
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "gist-star-scan-script",
        "X-GitHub-Api-Version": "2026-03-10",
    }
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


def parse_last_page_from_link_header(link_header):
    # Example:
    # <.../forks?page=34>; rel="last", <.../forks?page=2>; rel="next"
    if not link_header:
        return None
    parts = [p.strip() for p in link_header.split(",")]
    for part in parts:
        if 'rel="last"' not in part:
            continue
        left = part.split(";")[0].strip()
        if not (left.startswith("<") and left.endswith(">")):
            continue
        url = left[1:-1]
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        page_values = query.get("page", [])
        if not page_values:
            continue
        try:
            return int(page_values[0])
        except ValueError:
            return None
    return None


def fetch_user_gists(session, username):
    gists = []
    page = 1
    while True:
        api_url = f"https://api.github.com/users/{username}/gists"
        response = session.get(api_url, params={"per_page": 100, "page": page}, timeout=30)
        if response.status_code == 404:
            return []
        response.raise_for_status()
        batch = response.json()
        if not batch:
            break
        gists.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return gists


def fetch_gist_forks_count(session, gist_id):
    # API endpoint: GET /gists/{gist_id}/forks
    forks_url = f"https://api.github.com/gists/{gist_id}/forks"
    response = session.get(forks_url, params={"per_page": 1, "page": 1}, timeout=30)
    if response.status_code == 404:
        return 0
    response.raise_for_status()
    batch = response.json()
    if not batch:
        return 0

    last_page = parse_last_page_from_link_header(response.headers.get("Link", ""))
    if last_page is not None:
        return last_page
    # No Link header but one item returned -> at least 1 fork.
    return len(batch)


def fetch_gist_starred_by_viewer(session, gist_id):
    # API endpoint: GET /gists/{gist_id}/star
    # 204 = starred by current token user, 404 = not starred or inaccessible.
    star_url = f"https://api.github.com/gists/{gist_id}/star"
    response = session.get(star_url, timeout=30)
    if response.status_code == 204:
        return True
    if response.status_code == 404:
        return False
    response.raise_for_status()
    return False


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
        return {"users": {}, "gists": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"users": {}, "gists": {}}
        data.setdefault("users", {})
        data.setdefault("gists", {})
        return data
    except (OSError, json.JSONDecodeError):
        return {"users": {}, "gists": {}}


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

        enriched = []
        user_gist_hits = 0
        user_gist_misses = 0
        for gist in gists[: args.max_gists_per_user]:
            gist_url = gist.get("html_url")
            if not gist_url:
                continue
            gist_id = gist.get("id")
            if not gist_id:
                continue
            cached_social = None
            if not args.refresh_gist_social:
                cached_social = cache["gists"].get(gist_id)

            if cached_social is not None:
                stars = cached_social.get("stars")
                forks = int(cached_social.get("forks", 0))
                gist_comments = int(cached_social.get("comments", gist.get("comments", 0)))
                starred_by_viewer = cached_social.get("starred_by_viewer")
                gist_cache_hit = True
            else:
                gist_comments = int(gist.get("comments", 0))
                stars = None  # Not provided by official REST gist responses.
                starred_by_viewer = None
                try:
                    forks = fetch_gist_forks_count(session, gist_id)
                except requests.RequestException as exc:
                    print(f"[{username}] Failed to fetch forks count for gist {gist_id}: {exc}")
                    forks = 0

                if args.include_starred_status:
                    try:
                        starred_by_viewer = fetch_gist_starred_by_viewer(session, gist_id)
                    except requests.RequestException as exc:
                        print(
                            f"[{username}] Failed to fetch starred status for gist {gist_id}: {exc}"
                        )
                        starred_by_viewer = None

                gist_cache_hit = False
                cache["gists"][gist_id] = {
                    "stars": stars,
                    "forks": forks,
                    "comments": gist_comments,
                    "starred_by_viewer": starred_by_viewer,
                }
                time.sleep(max(args.sleep_ms, 0) / 1000.0)

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
                    "starred_by_viewer": starred_by_viewer,
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
            key=lambda item: (item["forks"], item["comments"], item["files"]),
            reverse=True,
        )[: args.top_n]

        print(f"[{username}] Top {len(top)} gist(s) by forks/comments")
        for idx, gist in enumerate(top, start=1):
            print(f"{idx}. {gist['description']}")
            print(f"   URL: {gist['url']}")
            stars_display = "n/a (REST API does not expose star count)"
            if gist["stars"] is not None:
                stars_display = str(gist["stars"])
            starred_status = "n/a"
            if args.include_starred_status:
                if gist["starred_by_viewer"] is True:
                    starred_status = "yes"
                elif gist["starred_by_viewer"] is False:
                    starred_status = "no"
            print(
                f"   Stars: {stars_display} | Forks: {gist['forks']} | "
                f"Comments: {gist['comments']} | Files: {gist['files']} | "
                f"Updated: {format_date(gist['updated_at'])}"
            )
            if args.include_starred_status:
                print(f"   Starred by token user: {starred_status}")
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
