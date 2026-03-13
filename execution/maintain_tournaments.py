import json
import logging
import os
from datetime import datetime

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

REFINED_FILE = "data/downloads/uscf_tournaments_refined.json"
CRAWL_URLS_FILE = ".tmp/tournament_urls.txt"

def main():
    if not os.path.exists(REFINED_FILE):
        logging.error(f"Refined file {REFINED_FILE} not found.")
        return

    with open(REFINED_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Load recent crawl URLs for removal detection
    recent_urls = set()
    if os.path.exists(CRAWL_URLS_FILE):
        with open(CRAWL_URLS_FILE, "r", encoding="utf-8") as f:
            recent_urls = {line.strip() for line in f if line.strip()}
    
    today = datetime.now().date()
    today_str = today.isoformat()
    
    updated_count = 0
    expired_count = 0
    
    for entry in data:
        url = entry.get('url')
        start_date_str = entry.get('startDate')
        end_date_str = entry.get('endDate')
        current_status = entry.get('status')
        
        # 1. Status Aging (upcoming -> ongoing -> finished)
        if start_date_str:
            # Fallback: if endDate is missing, assume it's a 1-day tournament
            effective_end_date_str = end_date_str if end_date_str else start_date_str
            try:
                # Dates are usually YYYY-MM-DD from AI refinement
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
            except Exception as e:
                logging.warning(f"  [Error] Could not parse dates for {entry.get('title')}: {e}")

        # 2. Expiration/Removal Detection
        # If it was "upcoming" but it's no longer in the crawl list, it's likely cancelled
        if current_status == "upcoming" and recent_urls and url not in recent_urls:
            # Check if the start date is still in the future
            if start_date_str:
                try:
                    start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
                    if start_date >= today:
                        entry['status'] = "expired"
                        expired_count += 1
                        logging.info(f"  [Removal] {entry.get('title')} marked as expired (Removed from USCF site)")
                except:
                    pass

    # 3. Pruning: Remove finished tournaments older than 7 days
    initial_count = len(data)
    from datetime import timedelta
    
    # We filter the list to keep only those that AREN'T old finished ones
    new_data = []
    for entry in data:
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
            new_data.append(entry)
            
    pruned_count = len(data) - len(new_data)
    data = new_data
    
    if pruned_count > 0:
        logging.info(f"  [Pruning] Removed {pruned_count} old finished tournaments.")

    if updated_count > 0 or expired_count > 0 or pruned_count > 0:
        with open(REFINED_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        logging.info(f"Maintenance complete. {updated_count} statuses updated, {expired_count} tournaments expired, {pruned_count} pruned.")
    else:
        logging.info("Maintenance complete. No changes needed.")

if __name__ == "__main__":
    main()
