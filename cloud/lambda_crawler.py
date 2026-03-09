import hashlib
import json
import logging
import os

import boto3
from playwright.sync_api import sync_playwright

# In a Lambda (Docker), we use the official Playwright package but need specific flags.


SQS_QUEUE_URL = os.environ.get("SQS_QUEUE_URL")
S3_BUCKET = os.environ.get("RESULT_BUCKET")
sqs = boto3.client('sqs')
s3 = boto3.client('s3')

def get_existing_hashes():
    """List all hashes of already refined tournaments in S3 master JSON."""
    hashes = set()
    try:
        response = s3.get_object(Bucket=S3_BUCKET, Key='uscf_tournaments_refined.json')
        data = json.loads(response['Body'].read().decode('utf-8'))
        for entry in data:
            url = entry.get('url')
            if url:
                hashes.add(hashlib.sha256(url.encode()).hexdigest())
    except Exception as e:
        if 'NoSuchKey' not in str(e):
            logging.warning(f"Could not load master JSON from S3 (might not exist yet): {e}")
    return hashes

def handler(event, context):
    """
    Lambda task: Crawl USCF site and push found URLs to SQS only if they don't exist in S3.
    """
    logging.info("Starting USCF Crawl...")
    existing_hashes = get_existing_hashes()
    logging.info(f"Found {len(existing_hashes)} existing tournament results in S3.")

    all_urls = []
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
                if link not in all_urls:
                    # Delta Check: Hash the URL and see if it's in S3
                    url_hash = hashlib.sha256(link.encode()).hexdigest()
                    if url_hash not in existing_hashes:
                        all_urls.append(link)
            
            # Next Button
            next_btn = page.query_selector('a[title="Go to next page"]')
            if not next_btn: break
            
            next_href = next_btn.get_attribute("href")
            if not next_href: break
            
            from urllib.parse import urljoin
            current_url = urljoin(base_url, next_href)

        browser.close()

    logging.info(f"Discovered {len(all_urls)} NEW tournaments to process.")

    # Push to SQS in batches
    for i in range(0, len(all_urls), 10):
        batch = all_urls[i:i+10]
        entries = [
            {'Id': str(j), 'MessageBody': url}
            for j, url in enumerate(batch)
        ]
        sqs.send_message_batch(QueueUrl=SQS_QUEUE_URL, Entries=entries)
        
    return {
        "statusCode": 200,
        "body": f"Queued {len(all_urls)} new tournaments."
    }
