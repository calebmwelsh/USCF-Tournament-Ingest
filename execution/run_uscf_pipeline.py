import argparse
import logging
import os
import subprocess
import sys
from datetime import datetime


def setup_logging():
    """Sets up logging to both console and a timestamped file in data/logs."""
    os.makedirs("data/logs", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = f"data/logs/pipeline_{timestamp}.log"
    
    # Configure logging
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # Clear existing handlers if any
    if logger.handlers:
        logger.handlers.clear()
        
    # File handler
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(file_handler)
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(console_handler)
    
    logging.info(f"Logging initialized. File: {log_file}")

def run_stage(name, script_path, args=[]):
    logging.info(f"\n{'='*20} STAGE: {name} {'='*20}")
    logging.info(f"Command: python -u {script_path} {' '.join(args)}")
    
    try:
        # Using -u for unbuffered output to see progress in real-time
        process = subprocess.Popen(
            [sys.executable, "-u", script_path] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        
        for line in process.stdout:
            print(f"  [{name}] {line.strip()}")
            
        process.wait()
        
        if process.returncode != 0:
            logging.error(f"Stage {name} failed with return code {process.returncode}")
            return False
        
        logging.info(f"Stage {name} completed successfully.")
        return True
    except Exception as e:
        logging.error(f"Error running stage {name}: {e}")
        return False

def main():
    setup_logging()
    parser = argparse.ArgumentParser(description="USCF Unified Data Pipeline Orchestrator")
    parser.add_argument('--test', action='store_true', help="Run a quick test pass.")
    parser.add_argument('--pages', type=int, help="Limit number of search pages crawled.")
    parser.add_argument('--tournaments', type=int, help="Limit number of tournaments processed.")
    args = parser.parse_args()

    # Step 0: Gather URLs with Playwright (more robust for dynamic content)
    logging.info("Gathering tournament URLs using Playwright...")
    if not run_stage("CRAWL", "execution/crawl_uscf_playwright.py"):
         logging.error("Pipeline aborted at CRAWL stage.")
         return

    # Step 1: Scrape details from gathered URLs
    scrape_args = ["--urls-file", ".tmp/tournament_urls.txt"]
    if args.test:
        scrape_args.extend(["--limit-tournaments", "5"])
    else:
        if args.tournaments:
            scrape_args.extend(["--limit-tournaments", str(args.tournaments)])
            
    if not run_stage("SCRAPE", "execution/scrape_uscf.py", scrape_args):
        logging.error("Pipeline aborted at SCRAPE stage.")
        return


    # Step 2: Deduplicate
    if not run_stage("DEDUPLICATE", "execution/deduplicate_uscf.py"):
        logging.error("Pipeline aborted at DEDUPLICATE stage.")
        return

    # Step 3: Refinement (AI)
    # The refinement script handles its own limits if needed
    if not run_stage("REFINE", "execution/refine_uscf_ai.py"):
        logging.info("Refinement stage completed with warnings or was manually stopped (Progress saved).")

    # Step 4: Maintenance (Status updates and cleanup)
    if not run_stage("MAINTAIN", "execution/maintain_tournaments.py"):
        logging.error("Pipeline aborted at MAINTAIN stage.")
        return

    logging.info("Pipeline completed successfully!")

if __name__ == "__main__":
    main()
