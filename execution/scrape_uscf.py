import argparse
import hashlib
import json
import os
import re
import time
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


def deobfuscate_cf_email(hex_str):
    """Decrypt Cloudflare obfuscated email."""
    try:
        k = int(hex_str[:2], 16)
        email = ''.join([chr(int(hex_str[i:i+2], 16) ^ k) for i in range(2, len(hex_str), 2)])
        return email
    except:
        return "[email protected]"

def clean_text(text):
    """Remove extra whitespace and newlines from scraped text."""
    if not text:
        return ""
    # Replace non-breaking spaces and other weird whitespace
    text = text.replace('\xa0', ' ')
    return re.sub(r'\s+', ' ', text).strip()

def extract_raw_data(soup, url):
    """Extracts raw content blocks for the AI to parse later."""
    # Handle Cloudflare email obfuscation before getting text
    for encrypted in soup.select('.__cf_email__'):
        hex_data = encrypted.get('data-cfemail')
        if hex_data:
            real_email = deobfuscate_cf_email(hex_data)
            encrypted.replace_with(real_email)

    # Title
    title_elem = soup.select_one('#block-pagetitle h1') or soup.select_one('h1.page-title') or soup.find('h1')
    title = clean_text(title_elem.text) if title_elem else "Unknown Title"

    # Meta tags as context
    meta_tags = []
    for tag in soup.find_all('meta', property=re.compile(r'^og:')):
        meta_tags.append(f"{tag.get('property')}: {tag.get('content')}")
    
    meta_text = clean_text(" | ".join(meta_tags))

    # Grab the entire main content block to ensure we don't miss anything (Organizer, Address Unit, etc.)
    # The USCF TLA pages usually wrap the content in these containers
    content_container = soup.select_one('.node-tournament-life-announcement') or \
                        soup.select_one('article') or \
                        soup.select_one('.region-content') or \
                        soup.select_one('#block-uschess-content')
    
    if content_container:
        # Get all text but maintain some structure with separators
        full_text = clean_text(content_container.get_text(' | '))
    else:
        full_text = clean_text(soup.get_text(' | '))

    raw_content = f"TITLE: {title} | URL: {url} | [META DATA] {meta_text} | [FULL PAGE CONTENT] {full_text}"

    # Content hash (primary delta signal) — SHA256 of raw_content
    # If this hasn't changed, the AI output wouldn't change either.
    content_hash = hashlib.sha256(raw_content.encode('utf-8')).hexdigest()

    # Modification time (secondary delta signal) — from og:updated_time meta tag
    modified_at = None
    og_updated = soup.find('meta', attrs={'property': 'og:updated_time'})
    art_modified = soup.find('meta', attrs={'property': 'article:modified_time'})
    
    if og_updated:
        modified_at = og_updated.get('content')
    elif art_modified:
        modified_at = art_modified.get('content')

    return {
        "url": url,
        "title": title,
        "raw_content": raw_content,
        "scrapedAt": datetime.utcnow().isoformat() + "Z",
        "contentHash": content_hash,
        "modifiedAt": modified_at
    }

def scrape_tournament(url=None, html_file=None):
    """Main entry point for scraping a single tournament."""
    html_content = ""
    if url:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'}
        try:
            r = requests.get(url, headers=headers)
            r.raise_for_status()
            html_content = r.text
        except Exception as e:
            print(f"Error fetching URL {url}: {e}")
            return None
    elif html_file:
        try:
            with open(html_file, 'r', encoding='utf-8') as f:
                html_content = f.read()
            soup_temp = BeautifulSoup(html_content, 'html.parser')
            canonical = soup_temp.find('link', rel='canonical')
            if canonical:
                 url = canonical['href']
            else:
                 url = f"file://{os.path.abspath(html_file)}"
        except Exception as e:
            print(f"Error reading file {html_file}: {e}")
            return None
    
    if not html_content:
        return None

    soup = BeautifulSoup(html_content, 'html.parser')
    return extract_raw_data(soup, url)

def crawl_tournaments(base_url, max_pages=None):
    """Crawl the USCF upcoming tournaments list and extract detail links."""
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'}
    current_url = base_url
    tournament_urls = []
    page_count = 0

    print(f"Starting crawl from: {current_url}")

    while current_url and (max_pages is None or page_count < max_pages):
        page_count += 1
        print(f"Fetching page {page_count}: {current_url}")
        
        try:
            r = requests.get(current_url, headers=headers)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, 'html.parser')
            
            # Extract tournament detail links
            links = soup.select('.views-row .views-field-title a')
            for link in links:
                href = link.get('href')
                if href:
                    full_url = urljoin(current_url, href)
                    if full_url not in tournament_urls:
                        tournament_urls.append(full_url)
            
            # Find next page link
            next_button = soup.select_one('li.pager__item--next a')
            if next_button:
                current_url = urljoin(current_url, next_button.get('href'))
            else:
                current_url = None
                
            # Rate limiting between page fetches
            time.sleep(1.0)
            
        except Exception as e:
            print(f"Error crawling page {page_count}: {e}")
            break

    print(f"Crawl complete. Found {len(tournament_urls)} tournament URLs.")
    return tournament_urls

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="USCF Tournament Scraper")
    parser.add_argument('--url', help="URL of a single USCF tournament page to scrape.")
    parser.add_argument('--file', help="Path to local HTML file to parse.")
    parser.add_argument('--urls-file', help="Path to a text file containing list of URLs to scrape.")
    parser.add_argument('--all', action='store_true', help="Crawl all upcoming tournaments (experimental).")
    parser.add_argument('--limit-pages', type=int, help="Limit number of pages to crawl.")
    parser.add_argument('--limit-tournaments', type=int, help="Limit number of tournaments to scrape.")
    args = parser.parse_args()

    os.makedirs('.tmp', exist_ok=True)
    results = []
    
    # Delta Scraping: Load existing progress to skip duplicates
    output_file = '.tmp/uscf_tournaments.json'
    scraped_urls = set()
    if os.path.exists(output_file):
        try:
            with open(output_file, 'r', encoding='utf-8') as f:
                existing_data = json.load(f)
                results = existing_data
                scraped_urls = {item['url'] for item in existing_data if 'url' in item}
                print(f"Loaded {len(scraped_urls)} existing tournaments. Will skip duplicates.")
        except Exception as e:
            print(f"Warning: Could not load existing progress: {e}")

    tournament_urls = []
    
    if args.urls_file:
         if os.path.exists(args.urls_file):
              with open(args.urls_file, 'r', encoding='utf-8') as f:
                   tournament_urls = [line.strip() for line in f if line.strip()]
         else:
              print(f"Error: URL file {args.urls_file} not found.")

    elif args.all:
        base_list_url = "https://new.uschess.org/upcoming-tournaments"
        tournament_urls = crawl_tournaments(base_list_url, max_pages=args.limit_pages)
        
    elif args.file:
        data = scrape_tournament(html_file=args.file)
        if data:
            results.append(data)
    elif args.url:
        data = scrape_tournament(url=args.url)
        if data:
            results.append(data)
    else:
        # Default test URL
        target_url = "https://new.uschess.org/climb-rating-ladder-online-courses-rated-games-analysis"
        data = scrape_tournament(url=target_url)
        if data:
            results.append(data)

    # Process URL list if collected
    if tournament_urls:
        if args.limit_tournaments:
            tournament_urls = tournament_urls[:args.limit_tournaments]
            
        print(f"Scraping {len(tournament_urls)} tournaments...")
        output_file = '.tmp/uscf_tournaments.json'
        
        for i, url in enumerate(tournament_urls):
            if url in scraped_urls:
                # No print here to avoid flooding terminal for 800+ entries
                continue
                
            print(f"[{i+1}/{len(tournament_urls)}] Processing: {url}")
            data = scrape_tournament(url=url)
            if data:
                results.append(data)
                scraped_urls.add(url)
            
            # Incremental save every 10 tournaments
            if (i + 1) % 10 == 0:
                with open(output_file, 'w', encoding='utf-8') as f:
                    json.dump(results, f, indent=4, ensure_ascii=False)
                print(f"  [Progress] {i+1} tournaments saved to {output_file}")
                
            time.sleep(1.5) # Respectful delay
    
    if results:
        output_file = '.tmp/uscf_tournaments.json'
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=4, ensure_ascii=False)
        print(f"Success! Final total {len(results)} tournaments saved to {output_file}")
    else:
        print("No data extracted.")
