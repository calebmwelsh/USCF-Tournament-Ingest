import json
import logging
import os
import random
import sys
import time
from datetime import datetime

from dotenv import load_dotenv
from google import genai
from google.genai import types

# Add root to path if running as script to find utils
if __name__ == "__main__":
    sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from utils.cost_tracker import tracker

# Load environment variables
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../.env/.env'), override=True)

# Setup basic logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def get_gemini_client(vertexai=False):
    if vertexai:
        vertex_key = os.getenv("VERTEX_API_KEY")
        if vertex_key:
            # Connect directly to the Vertex AI endpoint using the API Key
            # The SDK will infer project/location from GOOGLE_CLOUD_PROJECT environment variables
            return genai.Client(api_key=vertex_key, vertexai=True)
        
        # Default to ADC for Vertex AI
        project = os.getenv("GOOGLE_CLOUD_PROJECT")
        location = os.getenv("GOOGLE_CLOUD_LOCATION")
        if project and location:
            return genai.Client(
                vertexai=True, 
                project=project,
                location=location
            )
        return None
    
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logging.error("GEMINI_API_KEY not found in .env/.env")
        return None
    
    return genai.Client(api_key=api_key)

import hashlib


def generate_id(title, date, location):
    """Generate a consistent ID based on content (Title + Date + Location)."""
    t = str(title or "").strip().lower()
    d = str(date or "").strip().lower()
    l = str(location or "").strip().lower()
    key = f"{t}|{d}|{l}"
    hash_object = hashlib.sha256(key.encode())
    return f"uscf-{hash_object.hexdigest()[:32]}"

def get_system_prompt():
    """Returns the content of the system prompt file."""
    prompt_path = "data/system_prompts/system_prompt.md"
    if os.path.exists(prompt_path):
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    
    # Fallback if file missing
    return "You are a specialized chess tournament data extractor. Return JSON for all metadata fields."

def refine_entry(client, raw_content, model, vertex_client=None, vertex_model=None):
    if not raw_content:
        return None, False
    
    today_str = datetime.now().strftime("%A, %B %d, %Y")
    config = types.GenerateContentConfig(
        temperature=0.1,
        system_instruction=get_system_prompt(),
        response_mime_type="application/json"
    )
    
    prompt = f"TODAY'S DATE: {today_str}\n\nExtract ALL Tournament Data from this raw text:\n\n{raw_content}"
    
    was_429 = False
    try:
        response = client.models.generate_content(
            model=model,
            contents=[prompt],
            config=config
        )
        # Log usage
        if hasattr(response, 'usage_metadata'):
            tracker.log_usage(
                model_name=model,
                input_tokens=response.usage_metadata.prompt_token_count,
                output_tokens=response.usage_metadata.candidates_token_count
            )

        if response.text:
            return process_ai_response(response.text), False
    except Exception as e:
        error_str = str(e)
        if ("429" in error_str or "RESOURCE_EXHAUSTED" in error_str):
            was_429 = True
            if vertex_client and vertex_model:
                logging.warning(f"  [Fallback] Primary API quota exceeded (429). Trying Vertex AI ({vertex_model})...")
                try:
                    response = vertex_client.models.generate_content(
                        model=vertex_model,
                        contents=[prompt],
                        config=config
                    )
                    # Log usage for Vertex
                    if hasattr(response, 'usage_metadata'):
                        tracker.log_usage(
                            model_name=vertex_model,
                            input_tokens=response.usage_metadata.prompt_token_count,
                            output_tokens=response.usage_metadata.candidates_token_count
                        )

                    if response.text:
                        return process_ai_response(response.text), True
                except Exception as ve:
                    logging.error(f"  [Error] Vertex fallback attempt also failed: {ve}")
        else:
            logging.error(f"  [Error] AI refinement failed: {e}")
            
    return None, was_429

def process_ai_response(text):
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], dict):
            return parsed[0]
        if isinstance(parsed, dict):
            return parsed
    except Exception as e:
        logging.error(f"  [Error] Failed to parse AI JSON: {e}")
    return None

def main():
    client = get_gemini_client()
    if not client:
        return

    # Prefer environment variable for model, fallback to gemini-flash-latest
    model = os.getenv("GEMINI_TEXT_MODEL", "models/gemini-flash-latest")
    logging.info(f"Using Model: {model}")
    input_file = ".tmp/uscf_tournaments_deduplicated.json"
    output_dir = "data/downloads"
    output_file = os.path.join(output_dir, "uscf_tournaments_refined.json")
    
    os.makedirs(output_dir, exist_ok=True)

    if not os.path.exists(input_file):
        logging.error(f"Input file {input_file} not found.")
        return

    with open(input_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    logging.info(f"Loaded {len(data)} tournaments for AI refinement.")
    
    # Load existing progress if any (using URL as pivot for progress)
    refined_data = []
    if os.path.exists(output_file):
        with open(output_file, "r", encoding="utf-8") as f:
            try:
                refined_data = json.load(f)
            except:
                pass

    refined_lookup = {e.get('url'): e for e in refined_data if e.get('url')}
    logging.info(f"Resuming from existing progress: {len(refined_lookup)} entries.")
    
    # Load model pool
    model_pool = os.getenv("GEMINI_MODELS_POOL", "models/gemini-flash-latest").split(',')
    vertex_model = os.getenv("VERTEX_TEXT_MODEL", "gemini-1.5-flash-002")
    vertex_client = get_gemini_client(vertexai=True)
    
    logging.info(f"Model pool: {model_pool}")
    logging.info(f"Vertex Model: {vertex_model}")
    
    current_model_idx = 0
    use_vertex_permanent = False

    # Iterate through latest crawl data and update refined_lookup.
    # Skip logic uses two signals:
    #   PRIMARY:   contentHash — SHA256 of raw_content. If unchanged, AI output won't change.
    #   SECONDARY: modifiedAt  — og:updated_time from the page. Useful but can fire on trivial CMS saves.
    # Skip if EITHER signal confirms no real change (hash match is sufficient on its own).
    
    for i, entry in enumerate(data):
        url = entry.get('url')
        existing_entry = refined_lookup.get(url)
        new_hash = entry.get('contentHash')
        new_modified_at = entry.get('modifiedAt')

        if existing_entry:
            existing_hash = existing_entry.get('contentHash')
            existing_modified_at = existing_entry.get('modifiedAt')

            # PRIMARY: content hash match → definitely unchanged, skip AI
            if new_hash and existing_hash and new_hash == existing_hash:
                logging.info(f"[{i+1}/{len(data)}] Skipping (hash match): {entry.get('title')}")
                existing_entry["scrapedAt"] = entry.get("scrapedAt")
                continue

            # SECONDARY: modifiedAt match (no hash yet from old entries) → likely unchanged, skip AI
            if not existing_hash and new_modified_at and existing_modified_at and new_modified_at == existing_modified_at:
                logging.info(f"[{i+1}/{len(data)}] Skipping (modifiedAt match, no prior hash): {entry.get('title')}")
                existing_entry["scrapedAt"] = entry.get("scrapedAt")
                continue

        logging.info(f"[{i+1}/{len(data)}] AI Parsing/Refining: {entry.get('title')}")
        
        refined_fields = None
        
        # If we already switched to Vertex permanently, use it directly
        if use_vertex_permanent and vertex_client:
            logging.info(f"  [AI] Using Vertex AI (Sticky Mode): {vertex_model}")
            refined_fields, _ = refine_entry(vertex_client, entry.get("raw_content"), vertex_model)
        else:
            # Cycle through model pool if 429 hit
            for _ in range(len(model_pool)):
                current_model = model_pool[current_model_idx]
                logging.info(f"  [AI] Using Pool: {current_model}")
                
                # Check for Google GenAI client (Gemini) vs potential other logic
                # For simplicity, assumed get_gemini_client returns appropriate client
                # The 'client' variable is already initialized outside the loop.
                refined_fields, was_429_hit = refine_entry(client, entry.get("raw_content"), current_model, vertex_client, vertex_model)
                
                if refined_fields:
                    break
                
                if was_429_hit: # Changed from error_code == 429 to was_429_hit
                    logging.warning(f"  [Fallback] Primary API quota exceeded (429). Trying next...")
                    current_model_idx = (current_model_idx + 1) % len(model_pool)
                    # If we've circled back to the start, try Vertex permanently
                    if current_model_idx == 0:
                        logging.warning("  [Sticky] All Gemini pool models hit rate limits. Switching to Vertex...")
                        use_vertex_permanent = True
                        break
                else:
                    # Non-quota error, don't necessarily skip model
                    break
            
            # Final fallback to Vertex if pool failed
            if not refined_fields and not use_vertex_permanent and vertex_client:
                logging.info(f"  [Fallback] Trying Vertex AI: {vertex_model}")
                refined_fields, _ = refine_entry(vertex_client, entry.get("raw_content"), vertex_model)

        if refined_fields:
            # Prepare final structure
            final_entry = {
                "id": None,
                "source": "uscf",
                "url": url,
                "scrapedAt": entry.get("scrapedAt"),
                "status": "upcoming",
                "region": "us"
            }
            # Add all AI fields
            final_entry.update(refined_fields)
            
            # Preserve contentHash (primary) and modifiedAt (secondary) from scrape stage
            final_entry["contentHash"] = entry.get("contentHash")
            final_entry["modifiedAt"] = entry.get("modifiedAt")
            
            # Generate the reliable ID based on AI-cleaned metadata
            final_entry["id"] = generate_id(
                final_entry.get("title"), 
                final_entry.get("startDate"), 
                final_entry.get("location")
            )
            
            if existing_entry:
                # Update in place to preserve its position in clarified_data list
                existing_entry.update(final_entry)
            else:
                # New entry
                refined_data.append(final_entry)
                refined_lookup[url] = final_entry
            
            # Incremental save
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(refined_data, f, indent=4, ensure_ascii=False)
            logging.info(f"  [Progress] Saved {len(refined_data)} refined tournaments.")
        else:
            logging.error(f"  [Skip] Failed to refine {entry.get('title')} after trying all models.")
        
        # Base sleep
        time.sleep(7.5)

    # Final save (redundant but safe)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(refined_data, f, indent=4, ensure_ascii=False)
    
    logging.info(f"Success! {len(refined_data)} refined tournaments saved to {output_file}")

if __name__ == "__main__":
    main()
