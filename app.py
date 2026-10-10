
import os
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

TIMEOUT = (3, 8)
MAX_RESULTS = 6

OFFICIAL_DOMAINS = (
    "gov.in",
    "nic.in",
    "scholarships.gov.in",
    "education.gov.in",
    "pib.gov.in",
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


def build_search_query(claim):
    text = claim.lower()

    if any(term in text for term in SCHOLARSHIP_TERMS):
        return (
            f'"National Scholarship Portal" {claim} '
            '(site:scholarships.gov.in OR site:education.gov.in '
            'OR site:gov.in OR site:nic.in)'
        )

    return claim


def search(query, engine):
    key = os.getenv("SERPAPI_KEY")
    if not key:
        raise RuntimeError(
            "SERPAPI_KEY is missing from Render Environment."
        )

    params = {
        "engine": engine,
        "q": query,
        "api_key": key,
        "gl": "in",
        "hl": "en",
        "num": MAX_RESULTS,
    }

    response = requests.get(
        "https://serpapi.com/search.json",
        params=params,
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()

    if data.get("error"):
        raise RuntimeError(data["error"])

    if engine == "google":
        items = data.get("organic_results", [])
    else:
        items = data.get("news_results", [])

    results = []

    for item in items:
        source = item.get("source", "")

        if isinstance(source, dict):
            source = source.get("name", "")

        link = item.get("link", "")

        if not link.startswith(("https://", "http://")):
            continue

        results.append({
            "title": item.get("title", "Untitled result"),
            "snippet": item.get("snippet", "No snippet provided."),
            "link": link,
            "source": source or urlparse(link).hostname or "",
            "date": item.get("date", ""),
            "official_source": is_official_url(link),
        })

    # Prefer official sources, without claiming that they prove the claim.
    results.sort(
        key=lambda item: not item["official_source"]
    )

    return results[:MAX_RESULTS]


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

    query = build_search_query(claim)
    web_results = []
    news_results = []
    search_errors = {}

    # Run both searches concurrently so their waits overlap.
    with ThreadPoolExecutor(max_workers=2) as executor:
        tasks = {
            "web": executor.submit(search, query, "google"),
            "news": executor.submit(search, claim, "google_news"),
        }

        for name, task in tasks.items():
            try:
                results = task.result(timeout=10)

                if name == "web":
                    web_results = results
                else:
                    news_results = results

            except Exception as exc:
                search_errors[name] = str(exc)
                app.logger.warning(
                    "%s search failed: %s", name, exc
                )

    total = len(web_results) + len(news_results)

    if total == 0:
        return jsonify({
            "claim": claim,
            "status": "Search unavailable",
            "error": (
                "No sources were retrieved. Check the SerpApi key, "
                "remaining searches, provider status, and Render logs."
            ),
            "explanation": (
                "A failed search does not mean the claim is false."
            ),
            "web_results": [],
            "news_results": [],
            "total_results": 0,
            "search_errors": search_errors,
        }), 502

    if search_errors:
        explanation = (
            "Some searches failed. Review the available evidence and "
            "the search errors before drawing a conclusion."
        )
    else:
        explanation = (
            "Search results are leads, not proof. Open the original "
            "sources, check their dates and credibility, and determine "
            "whether they directly support or contradict the claim."
        )

    return jsonify({
        "claim": claim,
        "status": "Unverified",
        "explanation": explanation,
        "web_results": web_results,
        "news_results": news_results,
        "total_results": total,
        "search_errors": search_errors,
    })


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
    )
