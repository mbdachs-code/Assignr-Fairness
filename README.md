# Assignr Hosted App

This folder contains the hosted MVP of the Assignr fairness workflow.

## What it does

- imports request rows from the Assignr games page using a bookmarklet
- refreshes assignment data from the Assignr API for a selected date range
- shows the final person-level report in the browser

## Files

- `app.py`: Flask app and hosted dashboard
- `assignr_client.py`: Assignr API client
- `templates/index.html`: hosted dashboard UI
- `render.yaml`: Render blueprint
- `.env.example`: environment variable template

## Local run

1. Create a virtual environment and install requirements.
2. Copy `.env.example` to `.env` and fill in the Assignr values.
3. Export the variables or load them with your preferred env tool.
4. Start the app:

```bash
flask --app app run --debug
```

By default it stores data in `assignr_hosted.db` in this folder.

## Deploy on Render

1. Create a new GitHub repo.
2. Copy the contents of this folder into that repo root.
3. Push the repo to GitHub.
4. In Render, create a new Blueprint deployment from the repo.
5. Render will read `render.yaml` and provision:
   - one Python web service
   - one Postgres database
6. In Render, set these environment variables:
   - `ASSIGNR_CLIENT_ID`
   - `ASSIGNR_CLIENT_SECRET`
   - `ASSIGNR_SITE_ID`
   - `PUBLIC_APP_URL`
7. Deploy.

## First-use flow

1. Open the hosted dashboard.
2. Add or copy the bookmarklet from the page.
3. In Assignr, open the games page and make the request rows visible.
4. Click the bookmarklet.
5. Back in the hosted dashboard, choose the date range.
6. Click `Refresh Final Report`.
7. Review the final report in the browser.

## Current scope

This first hosted version keeps the bookmarklet on purpose. It replaces the local watcher and local CSV-based UI, but it still expects request capture to happen from the Assignr games page in the browser.
