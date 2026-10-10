
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import requests
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "being", "to", "of", "in", "on", "at", "for", "by", "with",
    "and", "or", "that", "this", "it", "as", "from", "has", "have",
    "had", "will", "would", "can", "could", "should", "may", "might",
    "do", "does", "did", "not", "their", "its", "they", "them",
    "who", "what", "when", "where", "which", "how", "all", "any",
}

OFFICIAL_DOMAINS = (
    "gov.in",
    "nic.in",
    "scholarships.gov.in",
    "education.gov.in",
    "pib.gov.in",
    "ugc.gov.in",
)

def tokens(text):
    words = re.findall(r"[a-zA-Z0-9]+", text.lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 2}

def official(url):
    try:
        host = (urlparse(url).hostname or "").lower()
        return any(
            host == domain or host.endswith("." + domain)
            for domain in OFFICIAL_DOMAINS
        )
    except (ValueError, TypeError):
        return False

def search(query, engine):
    key = os.getenv("SERPAPI_KEY")
    if not key:
        raise RuntimeError("SERPAPI_KEY is missing in Render Environment.")

    response = requests.get(
        "https://serpapi.com/search.json",
        params={
            "engine": engine,
            "q": query,
            "api_key": key,
            "gl": "in",
            "hl": "en",
            "num": 10,
        },
        timeout=(3, 8),
    )
    response.raise_for_status()
    data = response.json()

    if data.get("error"):
        raise RuntimeError(data["error"])

    items = (
        data.get("news_results", [])
        if engine == "google_news"
        else data.get("organic_results", [])
    )

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
            "snippet": item.get("snippet", ""),
            "link": link,
            "source": source or urlparse(link).hostname or "",
            "date": item.get("date", ""),
            "official_source": official(link),
        })
    return results

def assess_relevance(claim, web_results, news_results):
    claim_words = tokens(claim)
    all_results = web_results + news_results
    scored = []

    for item in all_results:
        text = item["title"] + " " + item["snippet"]
        result_words = tokens(text)
        overlap = len(claim_words & result_words)
        score = overlap / max(len(claim_words), 1)

        item["relevance_score"] = round(score * 100)
        scored.append((score, item))

    relevant = [
        item for score, item in scored
        if score >= 0.20 and len(claim_words & tokens(
            item["title"] + " " + item["snippet"]
        )) >= 2
    ]

    if not relevant:
        return {
            "status": "Insufficient evidence",
            "confidence": "Low",
            "explanation": (
                "The retrieved results do not contain enough matching "
                "information to assess this claim. This does not mean "
                "the claim is false."
            ),
        }

    official_relevant = [
        item for item in relevant if item["official_source"]
    ]

    if official_relevant:
        return {
            "status": "Relevant official evidence found",
            "confidence": "Limited",
            "explanation": (
                "A potentially relevant official-domain source was found. "
                "Relevance is based on title and snippet matching only; "
                "the original page must be reviewed before deciding whether "
                "it supports or contradicts the claim."
            ),
        }

    return {
        "status": "Relevant evidence found",
        "confidence": "Limited",
        "explanation": (
            "Some search results match the claim's keywords. Their titles "
            "and snippets do not establish whether the claim is true. "
            "Open the original sources and check their evidence."
        ),
    }

@app.get("/")
def home():
    return render_template("index.html")

@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "app": "ClaimTrace India"})

@app.post("/api/verify")
def verify():
    body = request.get_json(silent=True) or {}
    claim = (body.get("claim") or "").strip()

    if len(claim) < 8:
        return jsonify({"error": "Enter a claim of at least 8 characters."}), 400
    if len(claim) > 500:
        return jsonify({"error": "Keep the claim under 500 characters."}), 400

    words = sorted(tokens(claim))
    keyword_query = " ".join(words[:10]) if words else claim

    jobs = {
        "web": (claim, "google"),
        "keywords": (keyword_query, "google"),
        "news": (claim, "google_news"),
    }
    found = {}
    errors = {}

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(search, query, engine): name
            for name, (query, engine) in jobs.items()
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                found[name] = future.result()
            except Exception as exc:
                app.logger.warning("%s search failed: %s", name, exc)
                errors[name] = str(exc)

    web_results = []
    seen = set()
    for item in found.get("web", []) + found.get("keywords", []):
        url = item["link"].rstrip("/")
        if url not in seen:
            seen.add(url)
            web_results.append(item)

    web_results.sort(key=lambda item: not item["official_source"])
    web_results = web_results[:6]
    news_results = found.get("news", [])[:6]

    total = len(web_results) + len(news_results)

    if total == 0:
        return jsonify({
            "claim": claim,
            "status": "Search unavailable",
            "explanation": (
                "No results were retrieved. Check SerpApi configuration, "
                "quota, provider status, and Render logs."
            ),
            "web_results": [],
            "news_results": [],
            "total_results": 0,
            "search_errors": errors,
        }), 502

    assessment = assess_relevance(claim, web_results, news_results)

    return jsonify({
        "claim": claim,
        "status": assessment["status"],
        "explanation": assessment["explanation"],
        "assessment": assessment,
        "web_results": web_results,
        "news_results": news_results,
        "total_results": total,
        "search_errors": errors,
    })

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
    )
