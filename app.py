
import os
from concurrent.futures import ThreadPoolExecutor
import requests
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

def search(query, engine):
    key = os.getenv("SERPAPI_KEY")
    if not key:
        raise RuntimeError("SERPAPI_KEY is not configured on the server.")

    response = requests.get(
        "https://serpapi.com/search.json",
        params={
            "engine": engine,
            "q": query,
            "api_key": key,
            "gl": "in",
            "hl": "en"
        },
        timeout=(3, 8)
    )
    response.raise_for_status()
    data = response.json()

    if data.get("error"):
        raise RuntimeError(data["error"])

    items = data.get(
        "organic_results" if engine == "google" else "news_results",
        []
    )

    results = []
    for item in items[:6]:
        source = item.get("source", "")
        if isinstance(source, dict):
            source = source.get("name", "")

        results.append({
            "title": item.get("title", "Untitled result"),
            "snippet": item.get("snippet", "No snippet provided."),
            "link": item.get("link", ""),
            "source": source or item.get("displayed_link", ""),
            "date": item.get("date", "")
        })

    return results

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
        return jsonify({"error": "Enter a complete claim of at least 8 characters."}), 400
    if len(claim) > 500:
        return jsonify({"error": "Keep the claim under 500 characters."}), 400

    results = {"web": [], "news": []}
    errors = {}

    with ThreadPoolExecutor(max_workers=2) as executor:
        tasks = {
            "web": executor.submit(search, claim, "google"),
            "news": executor.submit(search, claim, "google_news")
        }

        for name, task in tasks.items():
            try:
                results[name] = task.result(timeout=10)
            except Exception as exc:
                errors[name] = str(exc)
                app.logger.warning("%s search failed: %s", name, exc)

    total = len(results["web"]) + len(results["news"])

    if total == 0:
        return jsonify({
            "error": "Both searches failed or returned no results. Check SerpApi status, quota, and server logs.",
            "web_results": [],
            "news_results": [],
            "total_results": 0,
            "search_errors": errors
        }), 502

    return jsonify({
        "claim": claim,
        "status": "Unverified",
        "explanation": "Search results are leads, not proof. Open original sources and assess whether they directly support or contradict the claim.",
        "web_results": results["web"],
        "news_results": results["news"],
        "total_results": total,
        "search_errors": errors
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
