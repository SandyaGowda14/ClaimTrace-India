
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import requests
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

TIMEOUT = (3, 7)
MAX_RESULTS = 6

OFFICIAL_DOMAINS = (
    "gov.in",
    "nic.in",
    "scholarships.gov.in",
    "education.gov.in",
    "pib.gov.in",
    "ugc.gov.in",
    "aicte-india.org",
)

SCHOLARSHIP_TERMS = (
    "scholarship",
    "scholarships",
    "national scholarship portal",
    "student financial aid",
    "education scheme",
)

def is_official_url(url):
    try:
        host = (urlparse(url).hostname or "").lower()
        return any(
            host == domain or host.endswith("." + domain)
            for domain in OFFICIAL_DOMAINS
        )
    except (ValueError, TypeError):
        return False


def build_queries(claim):
    queries = {
        "web": claim,
        "news": claim,
    }

    text = claim.lower()

    if any(term in text for term in SCHOLARSHIP_TERMS):
        queries["official"] = (
            f'{claim} '
            '(site:scholarships.gov.in OR site:education.gov.in '
            'OR site:gov.in OR site:nic.in)'
        )
    else:
        queries["official"] = (
            f'{claim} (site:gov.in OR site:nic.in)'
        )

    return queries


def search(query, engine):
    key = os.getenv("SERPAPI_KEY")
    if not key:
        raise RuntimeError(
            "SERPAPI_KEY is missing from Render Environment."
        )

    response = requests.get(
        "https://serpapi.com/search.json",
        params={
            "engine": engine,
            "q": query,
            "api_key": key,
            "gl": "in",
            "hl": "en",
            "num": MAX_RESULTS,
        },
        timeout=TIMEOUT,
    )

    response.raise_for_status()
    data = response.json()

    if data.get("error"):
        raise RuntimeError(data["error"])

    if engine == "google_news":
        items = data.get("news_results", [])
    else:
        items = data.get("organic_results", [])

    results = []

    for item in items:
        link = item.get("link", "")

        if not link.startswith(("https://", "http://")):
            continue

        source = item.get("source", "")
        if isinstance(source, dict):
            source = source.get("name", "")

        results.append({
            "title": item.get("title", "Untitled result"),
            "snippet": item.get("snippet", "No snippet provided."),
            "link": link,
            "source": source or urlparse(link).hostname or "",
            "date": item.get("date", ""),
            "official_source": is_official_url(link),
        })

    return results


@app.get("/")
def home():
    return render_template("index.html")


@app.get("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "app": "ClaimTrace India",
    })


@app.post("/api/verify")
def verify():
    body = request.get_json(silent=True) or {}
    claim = (body.get("claim") or "").strip()

    if len(claim) < 8:
        return jsonify({
            "error": "Enter a complete claim of at least 8 characters."
        }), 400

    if len(claim) > 500:
        return jsonify({
            "error": "Keep the claim under 500 characters."
        }), 400

    queries = build_queries(claim)
    jobs = {
        "web": (queries["web"], "google"),
        "news": (queries["news"], "google_news"),
        "official": (queries["official"], "google"),
    }

    results_by_search = {}
    errors = {}

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(search, query, engine): name
            for name, (query, engine) in jobs.items()
        }

        for future in as_completed(futures):
            name = futures[future]
            try:
                results_by_search[name] = future.result()
            except Exception as exc:
                errors[name] = str(exc)
                app.logger.warning(
                    "%s search failed: %s", name, exc
                )

    web_results = []
    news_results = results_by_search.get("news", [])

    for name in ("official", "web"):
        web_results.extend(results_by_search.get(name, []))

    # Deduplicate results by URL.
    seen = set()
    unique_web = []

    for item in web_results:
        url = item["link"].rstrip("/")
        if url not in seen:
            seen.add(url)
            unique_web.append(item)

    unique_web.sort(
        key=lambda item: not item["official_source"]
    )

    seen_news = set()
    unique_news = []

    for item in news_results:
        url = item["link"].rstrip("/")
        if url not in seen_news:
            seen_news.add(url)
            unique_news.append(item)

    total = len(unique_web) + len(unique_news)

    if total == 0:
        return jsonify({
            "claim": claim,
            "status": "Search unavailable",
            "error": (
                "No results were retrieved. Check the API key, "
                "search quota, provider status, and server logs."
            ),
            "explanation": (
                "A failed search does not mean the claim is false."
            ),
            "web_results": [],
            "news_results": [],
            "total_results": 0,
            "search_errors": errors,
        }), 502

    return jsonify({
        "claim": claim,
        "status": "Unverified",
        "explanation": (
            "Results are evidence leads, not proof. Official-domain "
            "matches are prioritised, but each source must be checked "
            "for relevance, date, credibility, and direct support."
        ),
        "web_results": unique_web[:MAX_RESULTS],
        "news_results": unique_news[:MAX_RESULTS],
        "total_results": min(len(unique_web), MAX_RESULTS)
        + min(len(unique_news), MAX_RESULTS),
        "search_errors": errors,
    })


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
    )
