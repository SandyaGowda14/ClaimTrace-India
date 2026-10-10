# ClaimTrace India

Flask evidence-discovery app using live Google Search and Google News results via SerpApi.

## Run
Install dependencies with `pip install -r requirements.txt`, set `SERPAPI_KEY` in your environment, then run `python app.py`.

## Render
Deploy this repository as a Render Web Service. Build command: `pip install -r requirements.txt`. Start command: `gunicorn app:app`. Add `SERPAPI_KEY` in Render's Environment Variables; never commit the key.

`/api/health` is the health endpoint. This prototype labels claims Unverified by default because search snippets alone do not prove a claim.


project :https://claimtrace-india.onrender.com
