"""Builds results/report.html: one self-contained, offline page.

The page source lives in report/assets/ (index.html, style.css, app.js). This module inlines the CSS and JS and embeds
the site data as JSON. Stdlib only.
"""
import json
import os

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


def _read(name):
    with open(os.path.join(ASSETS, name), encoding="utf-8") as f:
        return f.read()


def _embed(data):
    # ensure_ascii plus escaping "<" means no "</script" or "<!--" can appear inside the data block.
    return json.dumps(data, default=list, ensure_ascii=True).replace("<", "\\u003c")


def build(data, path):
    html = _read("index.html")
    css, js = _read("style.css"), _read("app.js")
    for body in (css, js):
        assert "</script" not in body.lower() and "</style" not in body.lower(), "asset would close its tag early"
    link = '<link rel="stylesheet" href="style.css">'
    script = '<script src="app.js"></script>'
    assert link in html and script in html and "__DATA__" in html, "index.html is missing a build placeholder"
    html = html.replace(link, "<style>\n" + css + "\n</style>")
    html = html.replace(script, "<script>\n" + js + "\n</script>")
    html = html.replace("__DATA__", _embed(data))
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
