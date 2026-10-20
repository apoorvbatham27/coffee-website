
from pathlib import Path

ROOT = Path(__file__).parent
FOOTER = "{% include 'includes/copyright.html' %}"

folders = [
    ROOT / "admin",
    ROOT / "templates"
]

count = 0

for folder in folders:
    if not folder.exists():
        print(f"Folder not found: {folder}")
        continue

    for file in folder.rglob("*.html"):
        text = file.read_text(encoding="utf-8")

        if "includes/copyright.html" in text:
            continue

        if "</body>" not in text.lower():
            print(f"Skipped (no body tag): {file}")
            continue

        index = text.lower().rfind("</body>")
        text = text[:index] + FOOTER + "\n" + text[index:]

        file.write_text(text, encoding="utf-8")
        print(f"Updated: {file.relative_to(ROOT)}")
        count += 1

print(f"\nDone! Updated {count} HTML files.")
