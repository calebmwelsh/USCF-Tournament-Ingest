import hashlib
import json
import os

INPUT_FILE = ".tmp/uscf_tournaments.json"
OUTPUT_FILE = ".tmp/uscf_tournaments_deduplicated.json"

if not os.path.exists(INPUT_FILE):
    print(f"Error: {INPUT_FILE} not found.")
    exit(1)

with open(INPUT_FILE, "r", encoding="utf-8") as f:
    data = json.load(f)

unique_tournaments = {}
duplicate_count = 0

for entry in data:
    url = entry.get("url")
    if not url:
        continue
        
    if url in unique_tournaments:
        duplicate_count += 1
        continue
    
    unique_tournaments[url] = entry

final_list = list(unique_tournaments.values())

print(f"Original entries: {len(data)}")
print(f"Duplicates removed: {duplicate_count}")
print(f"Final unique entries: {len(final_list)}")

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(final_list, f, indent=4, ensure_ascii=False)

print(f"Saved to {OUTPUT_FILE}")
