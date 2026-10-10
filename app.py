import os
import requests
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

@app.get("/")
def home():
    return render_template("index.html")

@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "app": "ClaimTrace India"})

def search(query, engine):
    key = os.getenv("SERPAPI_KEY")
    if not key:
        raise RuntimeError("SERPAPI_KEY is not configured on the server.")
    r = requests.get("https://serpapi.com/search.json", params={
        "engine": engine, "q": query, "api_key": key, "num": 6, "gl": "in", "hl": "en"
    }, timeout=20)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise RuntimeError(data["error"])
    return [{
        "title": x.get("title", "Untitled result"),
        "snippet": x.get("snippet", "No snippet provided."),
        "link": x.get("link", ""),
        "source": x.get("source") or x.get("displayed_link") or ""
    } for x in data.get("organic_results", [])[:6]]

@app.post("/api/verify")
def verify():
    body = request.get_json(silent=True) or {}
    claim = (body.get("claim") or "").strip()
    if len(claim) < 8:
        return jsonify({"error": "Enter a complete claim of at least 8 characters."}), 400
    if len(claim) > 500:
        return jsonify({"error": "Keep the claim under 500 characters."}), 400
    try:
        web = search(claim, "google")
        news = search(claim, "google_news")
    except requests.Timeout:
        return jsonify({"error": "Search timed out. Please try again."}), 504
    except requests.RequestException:
        return jsonify({"error": "Search provider could not be reached. Please try again."}), 502
    except RuntimeError as e:
        msg = str(e)
        code = 503 if "not configured" in msg else 502
        return jsonify({"error": msg}), code
    total = len(web) + len(news)
    return jsonify({
        "claim": claim, "status": "Unverified",
        "explanation": "Search results are leads, not proof. Open the original sources and compare their credibility, dates, and evidence before deciding whether this claim is true or false.",
        "web_results": web, "news_results": news, "total_results": total
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
