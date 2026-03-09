# USCF Tournament Scraper Pipeline

> **Note on Architecture**: This project follows a 3-layer architecture consisting of Intent (`directives/`), Orchestration (Agent), and Execution (`execution/`). See global agent instructions for details.

This pipeline automates the extraction, deduplication, and AI-powered refinement of USCF tournament data.

## Prerequisites
1. **Python 3.10+**
2. **Playwright**: Installed via `pip install playwright` and `playwright install`.
3. **API Keys**: Setup `.env/.env` (see `.env/.env.example`).
   - *Note:* All credentials must live within the Centralized Credentials Directory (`.env/`).
4. **Vertex AI Auth**: If using Vertex fallback, run:
   ```bash
   gcloud auth application-default login
   ```

## How to Run (Start to Finish)

Run the master orchestrator script:
```bash
python execution/run_uscf_pipeline.py
```

### What happens inside?
The orchestrator runs these deterministic execution scripts in sequence:
1.  **CRAWL** (`execution/crawl_uscf_playwright.py`): Uses Playwright to gather tournament URLs from the USCF website.
2.  **SCRAPE** (`execution/scrape_uscf.py`): Fetches the raw HTML content. 
    *   *Optimization*: It skips any URL already found in `.tmp/uscf_tournaments.json`.
3.  **DEDUPLICATE** (`execution/deduplicate_uscf.py`): Removes redundant entries.
4.  **REFINE** (`execution/refine_uscf_ai.py`): Uses Gemini AI (with Vertex AI fallback) to structure metadata.
    *   *Cost Tracking*: Token usage and projected costs are logged to `.tmp/cost_tracking.json`.
    *   *Output*: Results saved to `data/downloads/uscf_tournaments_refined.json`.

*(Note: All intermediate processing data is stored in the `.tmp/` directory which is ignored by version control and can be safely deleted and regenerated).*

## Configuration Options
- **Test Run**: `python execution/run_uscf_pipeline.py --test` (processes a small sample).
- **Daily Updates**: Simply run the pipeline again. It will automatically skip existing records (Delta Scraping).

## Cloud Deployment
Refer to `cloud/` for AWS Lambda migration assets, including:
- `Dockerfile` for Playwright support.
- Parallelized SQS/Lambda handlers.
