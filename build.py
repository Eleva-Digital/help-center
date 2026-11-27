# Builds the help center page from src/help.src.html
#   ghl-custom-code.html  -> paste into the GHL Custom Code element (reads data from GitHub Pages)
#   docs/index.html       -> standalone copy served by GitHub Pages (reads ./data)
import base64, pathlib
here = pathlib.Path(__file__).parent
PAGES_DATA = "https://eleva-digital.github.io/help-center/data"
def uri(p): return "data:image/png;base64," + base64.b64encode((here/"assets"/p).read_bytes()).decode()
src = (here/"src/help.src.html").read_text(encoding="utf-8")
base = (src.replace("{{LOGO_WHITE}}", uri("eleva-logo-white.png"))
           .replace("{{LOGO_DARK}}", uri("eleva-logo-primary.png"))
           .replace("{{FAVICON}}", uri("eleva-favicon.png")))
(here/"ghl-custom-code.html").write_text(base.replace("{{DATA_URL}}", PAGES_DATA), encoding="utf-8")
full = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Eleva Help Center</title>
<meta name="description" content="Guides, tutorials and answers for everything in Eleva.">
<meta name="robots" content="noindex">
<link rel="icon" href="{uri('eleva-favicon.png')}">
<style>html,body{{margin:0;padding:0;background:#0A0C0B}}</style>
</head>
<body>
{base.replace("{{DATA_URL}}", "data")}
</body>
</html>
"""
(here/"docs").mkdir(exist_ok=True)
(here/"docs/index.html").write_text(full, encoding="utf-8")
print("built", len(base), "bytes snippet")
