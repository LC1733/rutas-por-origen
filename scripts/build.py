"""Build the portable GitHub Pages entry point from reviewed source files."""
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

def inline_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")

def build():
    html = (SRC / "index.html").read_text(encoding="utf-8")
    css = (SRC / "styles.css").read_text(encoding="utf-8")
    audit = json.loads((ROOT / "data/audit-data.json").read_text(encoding="utf-8"))
    live_path = ROOT / "data/live.json"
    live = json.loads(live_path.read_text(encoding="utf-8")) if live_path.exists() else None
    html = html.replace('<link rel="stylesheet" href="styles.css">', '<style>' + css + '</style>')
    html = re.sub(r'  <script src="(?:data|monitor|app)\.js" defer></script>\n', '', html)
    html = re.sub(r'  <link rel="icon"[^>]+>\n', '', html)
    scripts = 'window.MARTILINE_DATA=' + inline_json(audit) + ';\nwindow.MARTILINE_LIVE_INITIAL=' + inline_json(live) + ';'
    for filename in ["monitor.js", "app.js"]:
        scripts += '\n' + (SRC / filename).read_text(encoding="utf-8").replace('</script', '<\\/script')
    html = html.replace('</body>', '<script>' + scripts + '</script>\n</body>')
    (ROOT / "index.html").write_text(html, encoding="utf-8")
    # Preview output is not committed; GitHub Pages uses the root index.html.
    preview = ROOT / "dist"
    preview.mkdir(exist_ok=True)
    (preview / "index.html").write_text(html, encoding="utf-8")
    if live_path.exists():
        (preview / "data").mkdir(exist_ok=True)
        shutil.copyfile(live_path, preview / "data/live.json")
    print(json.dumps({"html": "index.html", "bytes": len(html.encode("utf-8")), "live_snapshot": bool(live)}))

if __name__ == "__main__":
    build()
