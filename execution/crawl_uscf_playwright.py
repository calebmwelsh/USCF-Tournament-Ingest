import asyncio
import os

from playwright.async_api import async_playwright

BASE_URL = "https://new.uschess.org/upcoming-tournaments"
OUTPUT_FILE = ".tmp/tournament_urls.txt"

async def main():
    os.makedirs(".tmp", exist_ok=True)
    all_urls = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        # Set a realistic user agent
        await page.set_extra_http_headers({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        })

        current_url = BASE_URL
        page_num = 0

        while True:
            print(f"[Page {page_num}] Navigating to {current_url}...")
            await page.goto(current_url, wait_until="domcontentloaded", timeout=60000)
            
            # Handle cookie consent modal if it appears
            try:
                accept_btn = await page.wait_for_selector(".cm-btn-accept-all", timeout=5000)
                if accept_btn:
                    print("  Closing cookie consent modal...")
                    await accept_btn.click()
                    await page.wait_for_timeout(1000)
            except:
                pass

            try:
                await page.wait_for_selector(".views-row", timeout=15000)
            except:
                print("  Timeout waiting for .views-row. Content might be missing.")
            
            await page.wait_for_timeout(2000)  # let any lazy loading finish

            # Try multiple selectors to be robust
            selectors = [
                 ".view-id-approved_tla_list .views-field-title a",
                 ".views-row h3 a",
                 ".views-row .event-details a",
                 ".views-row .views-field-nothing a",
                 ".views-row a"
            ]
            
            links = []
            for selector in selectors:
                found = await page.eval_on_selector_all(
                    selector,
                    "els => els.map(el => el.href)"
                )
                if found:
                    # Filter for likely tournament detail links (usually no query params, starts with /)
                    for f in found:
                         if f and "/upcoming-tournaments" not in f and "#" not in f:
                              if f not in links:
                                   links.append(f)
                
                if len(links) >= 5: # If we found a reasonable amount, stop
                     break

            if not links:
                print("  CRITICAL: Still no links found. Saving debug info...")
                await page.screenshot(path=".tmp/playwright_debug.png")
                print("  Saved .tmp/playwright_debug.png")

            new_count = 0
            for link in links:
                if link and link not in all_urls:
                    all_urls.append(link)
                    new_count += 1
            print(f"  Found {new_count} new links (total: {len(all_urls)})")



            # Scroll to bottom to ensure pager is loaded/visible
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1000)

            # Check for the Next button using more robust selectors
            next_btn = await page.query_selector('a[title="Go to next page"]')
            if not next_btn:
                # Try fallback
                next_btn = await page.query_selector('li.pager__item--next a')
            
            if not next_btn:
                print("No 'Next' button found. Done.")
                break

            next_href = await next_btn.get_attribute("href")
            if not next_href:
                print("Next button has no href. Done.")
                break

            from urllib.parse import urljoin
            current_url = urljoin(BASE_URL, next_href)
            page_num += 1

        await browser.close()

    print(f"\nTotal unique tournament URLs: {len(all_urls)}")
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for url in all_urls:
            f.write(url + "\n")
    print(f"Saved to {OUTPUT_FILE}")

asyncio.run(main())
