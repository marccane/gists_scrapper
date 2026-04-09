import argparse
import os
import re
import time
from datetime import datetime
from urllib.parse import urlparse

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
        help="Limit number of gists fetched per user for star parsing.",
    )
    parser.add_argument(
        "--sleep-ms",
        type=int,
        default=120,
        help="Sleep time between gist-page requests in milliseconds.",
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
    # Converts "1.2k" to 1200 and "987" to 987.
    cleaned = text.strip().lower().replace(",", "")
    if not cleaned:
        return 0
    multiplier = 1
    if cleaned.endswith("k"):
        multiplier = 1000
        cleaned = cleaned[:-1]
    elif cleaned.endswith("m"):
        multiplier = 1000000
        cleaned = cleaned[:-1]
    try:
        return int(float(cleaned) * multiplier)
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


def fetch_gist_social_counts(session, gist_url):
    response = session.get(gist_url, timeout=30)
    response.raise_for_status()
    html = response.text
    stars = extract_social_count(html, "stargazers")
    forks = extract_social_count(html, "network/members")
    return stars, forks


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


def main():
    args = parse_args()
    session = build_session()
    profile_urls = read_profile_urls(args.input_file)

    if args.max_users > 0:
        profile_urls = profile_urls[: args.max_users]

    if not profile_urls:
        print("No profile URLs found.")
        return

    print(f"Processing {len(profile_urls)} user(s) from {args.input_file}\n")

    for profile_url in profile_urls:
        username = extract_username(profile_url)
        if not username:
            print(f"Skipping invalid GitHub profile URL: {profile_url}")
            continue

        try:
            gists = fetch_user_gists(session, username)
        except requests.RequestException as exc:
            print(f"[{username}] Failed to fetch gist list: {exc}")
            print("---")
            continue

        if not gists:
            print(f"[{username}] No gists found.")
            print("---")
            continue

        enriched = []
        for gist in gists[: args.max_gists_per_user]:
            gist_url = gist.get("html_url")
            if not gist_url:
                continue
            try:
                stars, forks = fetch_gist_social_counts(session, gist_url)
            except requests.RequestException:
                stars, forks = 0, 0

            enriched.append(
                {
                    "description": gist.get("description") or "No description",
                    "url": gist_url,
                    "stars": stars,
                    "forks": forks,
                    "comments": gist.get("comments", 0),
                    "files": len(gist.get("files", {})),
                    "updated_at": gist.get("updated_at", ""),
                }
            )
            time.sleep(max(args.sleep_ms, 0) / 1000.0)

        if not enriched:
            print(f"[{username}] Could not fetch any gist stats.")
            print("---")
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
        print("---")


if __name__ == "__main__":
    main()
