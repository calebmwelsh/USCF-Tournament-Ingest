import hashlib
import json
import logging
import os
import random
import sys
import time

import boto3

# Add task root to sys.path to ensure imports work in Lambda
sys.path.append(os.environ.get('LAMBDA_TASK_ROOT', '/var/task'))

from execution.refine_uscf_ai import generate_id, get_gemini_client, refine_entry
from execution.scrape_uscf import scrape_tournament

S3_BUCKET = os.environ.get("RESULT_BUCKET")
s3 = boto3.client('s3')

# Standard logging config for Lambda
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()
logger.setLevel(logging.INFO)

def handler(event, context):
    """
    Triggered by SQS batch.
    Processes tournament URLs: Scrape -> AI Refine -> S3.
    """
    logging.info(f"Refiner received {len(event['Records'])} records.")
    
    client = get_gemini_client()
    vertex_client = get_gemini_client(vertexai=True)
    model = os.getenv("GEMINI_TEXT_MODEL", "models/gemini-2.0-flash-lite")
    vertex_model = os.getenv("VERTEX_TEXT_MODEL", "gemini-2.0-flash-001")
    
    # Reduced jitter for higher throughput (0-10s)
    # Combined Gemini + Vertex gives us more headroom
    jitter = random.uniform(0, 10)
    logging.info(f"Applying jitter: sleeping for {jitter:.1f}s...")
    time.sleep(jitter)
    
    for record in event['Records']:
        url = record['body']
        logging.info(f"Processing URL: {url}")
        
        try:
            # 1. Scrape raw content
            scraped_data = scrape_tournament(url=url)
            if not scraped_data or not scraped_data.get("raw_content"):
                logging.error(f"Failed to scrape content for {url}. Scraped data: {scraped_data}")
                continue
            
            logging.info(f"Scraped {len(scraped_data.get('raw_content'))} chars from {url}")
            
            # 2. Refine with AI (with Vertex fallback)
            refined_dict, was_429 = refine_entry(
                client, 
                scraped_data.get("raw_content"), 
                model,
                vertex_client=vertex_client,
                vertex_model=vertex_model
            )
            
            if not refined_dict:
                err_msg = f"AI refinement failed for {url}. 429 was hit: {was_429}"
                logging.error(err_msg)
                # Raise exception so SQS retries
                raise Exception(err_msg)
                
            # 3. Assemble final data
            final_entry = {
                "id": generate_id(
                    refined_dict.get("title"), 
                    refined_dict.get("startDate"), 
                    refined_dict.get("location")
                ),
                "source": "uscf",
                "url": url,
                "scrapedAt": scraped_data.get("scrapedAt"),
                "status": "upcoming",
                "region": "us"
            }
            final_entry.update(refined_dict)
            
            # 4. Save to single S3 master file
            file_key = "uscf_tournaments_refined.json"
            
            existing_data = []
            etag = None
            try:
                response = s3.get_object(Bucket=S3_BUCKET, Key=file_key)
                existing_data = json.loads(response['Body'].read().decode('utf-8'))
                etag = response.get('ETag')
                if not isinstance(existing_data, list):
                    existing_data = []
            except Exception as e:
                if 'NoSuchKey' not in str(e):
                    logging.warning(f"Error reading existing {file_key}: {e}")
            
            # Remove existing entry if it's an overwrite
            existing_data = [item for item in existing_data if item.get('url') != url]
            existing_data.append(final_entry)

            put_kwargs = {
                'Bucket': S3_BUCKET,
                'Key': file_key,
                'Body': json.dumps(existing_data, indent=4, ensure_ascii=False),
                'ContentType': 'application/json'
            }
            if etag:
                put_kwargs['IfMatch'] = etag
                
            from botocore.exceptions import ClientError
            try:
                s3.put_object(**put_kwargs)
            except ClientError as e:
                # 412 or PreconditionFailed denotes race condition
                if e.response['Error']['Code'] in ('PreconditionFailed', '412'):
                    raise Exception(f"Concurrent S3 write detected for {file_key}. Re-queueing...")
                raise e
            
            logging.info(f"Successfully appended refined tournament: {final_entry.get('title')} to master JSON")
            
        except Exception as e:
            logging.error(f"Error processing {url}: {e}")
            # Re-raise so SQS handles retry
            raise e
            
    return {"statusCode": 200}
