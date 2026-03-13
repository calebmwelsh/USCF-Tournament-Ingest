import hashlib
import json
import logging
import os

from datetime import datetime

import boto3
from playwright.sync_api import sync_playwright
# Standard logging config for Lambda
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# In a Lambda (Docker), we use the official Playwright package but need specific flags.


SQS_QUEUE_URL = os.environ.get("SQS_QUEUE_URL")
S3_BUCKET = os.environ.get("RESULT_BUCKET")
# Explicitly set region to ensure consistency as requested
REGION = "us-east-2"
sqs = boto3.client('sqs', region_name=REGION)
s3 = boto3.client('s3', region_name=REGION)

def get_existing_data():
    """Download and return the master JSON from S3, or empty list if not found."""
    try:
        response = s3.get_object(Bucket=S3_BUCKET, Key='uscf_tournaments_refined.json')
        return json.loads(response['Body'].read().decode('utf-8'))
    except Exception as e:
        if 'NoSuchKey' not in str(e):
            logging.warning(f"Could not load master JSON from S3 (might not exist yet): {e}")
    return []

def save_master_json(data):
    """Upload the updated master JSON to S3."""
    try:
        s3.put_object(
            Bucket=S3_BUCKET,
            Key='uscf_tournaments_refined.json',
            Body=json.dumps(data, indent=4, ensure_ascii=False).encode('utf-8'),
            ContentType='application/json'
        )
        logging.info("Successfully updated master JSON in S3.")
    except Exception as e:
        logging.error(f"Failed to save master JSON to S3: {e}")

def handler(event, context):
    """
    Lambda task: Crawl USCF site and push found URLs to SQS only if they don't exist in S3.
    """
    logging.info("Starting USCF Crawl and Maintenance...")
    master_data = get_existing_data()
    
    existing_hashes = set()
    for entry in master_data:
        url = entry.get('url')
        if url:
            existing_hashes.add(hashlib.sha256(url.encode()).hexdigest())

    logging.info(f"Found {len(existing_hashes)} existing tournament results in S3.")

    new_urls = []
    active_urls = set()
    base_url = "https://new.uschess.org/upcoming-tournaments"

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--single-process'
            ]
        )
        # In Lambda context, we usually need specific flags, but the Docker image handles most.
        page = browser.new_page()
        page.set_extra_http_headers({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        })

        current_url = base_url
        while True:
            logging.info(f"Navigating to {current_url}...")
            try:
                response = page.goto(current_url, wait_until="domcontentloaded", timeout=60000)
                logging.info(f"Page loaded with status: {response.status if response else 'N/A'}")
            except Exception as e:
                logging.error(f"Failed to navigate to {current_url}: {e}")
                # Take a screenshot if possible for debugging (stored in /tmp in Lambda)
                try:
                    page.screenshot(path="/tmp/error_screenshot.png")
                    logging.info("Error screenshot saved to /tmp/error_screenshot.png")
                except:
                    pass
                break
            
            # Found links
            selectors = [
                 ".view-id-approved_tla_list .views-field-title a",
                 ".views-row h3 a",
                 ".views-row .event-details a",
                 ".views-row a"
            ]
            
            links = []
            for selector in selectors:
                try:
                    found = page.eval_on_selector_all(selector, "els => els.map(el => el.href)")
                    if found:
                        for f in found:
                            if f and "/upcoming-tournaments" not in f and "#" not in f and f not in links:
                                links.append(f)
                except:
                    continue
                if len(links) >= 10: break

            for link in links:
                if link not in active_urls:
                    active_urls.add(link)
                    # Delta Check: Hash the URL and see if it's in S3
                    url_hash = hashlib.sha256(link.encode()).hexdigest()
                    if url_hash not in existing_hashes:
                        new_urls.append(link)
            
            # Next Button
            next_btn = page.query_selector('a[title="Go to next page"]')
            if not next_btn: break
            
            next_href = next_btn.get_attribute("href")
            if not next_href: break
            
            from urllib.parse import urljoin
            current_url = urljoin(base_url, next_href)

        browser.close()

    logging.info(f"Discovered {len(new_urls)} NEW tournaments to process. {len(active_urls)} active URLs total.")

    # --- Maintenance Phase ---
    today = datetime.now().date()
    updated_count = 0
    expired_count = 0
    
    for entry in master_data:
        url = entry.get('url')
        start_date_str = entry.get('startDate')
        end_date_str = entry.get('endDate')
        current_status = entry.get('status')
        
        # 1. Status Aging (upcoming -> ongoing -> finished)
        if start_date_str:
            # Fallback: if endDate is missing, assume it's a 1-day tournament
            effective_end_date_str = end_date_str if end_date_str else start_date_str
            try:
                start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
                end_date = datetime.strptime(effective_end_date_str, "%Y-%m-%d").date()
                
                new_status = current_status
                if end_date < today:
                    new_status = "finished"
                elif start_date <= today <= end_date:
                    new_status = "ongoing"
                
                if new_status != current_status:
                    entry['status'] = new_status
                    updated_count += 1
                    logging.info(f"  [Status] {entry.get('title')} -> {new_status}")
            except Exception:
                pass

        # 2. Expiration/Removal Detection
        if current_status == "upcoming" and active_urls and url not in active_urls:
            if start_date_str:
                try:
                    start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
                    if start_date >= today:
                        entry['status'] = "expired"
                        expired_count += 1
                        logging.info(f"  [Removal] {entry.get('title')} marked as expired (Removed from USCF site)")
                except Exception:
                    pass

    # 3. Pruning: Remove finished tournaments older than 7 days
    from datetime import timedelta
    new_master_data = []
    for entry in master_data:
        keep = True
        if entry.get('status') == "finished":
            date_str = entry.get('endDate') or entry.get('startDate')
            if date_str:
                try:
                    event_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                    if event_date < today - timedelta(days=7):
                        keep = False
                except:
                    pass
        if keep:
            new_master_data.append(entry)
            
    pruned_count = len(master_data) - len(new_master_data)
    master_data = new_master_data
    
    if pruned_count > 0:
        logging.info(f"  [Pruning] Removed {pruned_count} old finished tournaments.")

    if updated_count > 0 or expired_count > 0 or pruned_count > 0:
        save_master_json(master_data)
        logging.info(f"Maintenance complete. {updated_count} statuses updated, {expired_count} tournaments expired, {pruned_count} pruned.")
    else:
        logging.info("Maintenance complete. No changes needed.")
    # --- End Maintenance Phase ---

    # Push to SQS in batches
    for i in range(0, len(new_urls), 10):
        batch = new_urls[i:i+10]
        entries = [
            {'Id': str(j), 'MessageBody': url}
            for j, url in enumerate(batch)
        ]
        sqs.send_message_batch(QueueUrl=SQS_QUEUE_URL, Entries=entries)
        
    return {
        "statusCode": 200,
        "body": f"Queued {len(new_urls)} new tournaments. Updated {updated_count}, Expired {expired_count}, Pruned {pruned_count}."
    }
