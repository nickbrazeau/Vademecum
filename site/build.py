"""Build the project website for GitHub Pages: a landing page and the tutorials.

The tutorials are the Markdown files in docs/tutorials, rendered as they are, so the site
and the repository never disagree. Run:

    pip install markdown
    python site/build.py            # writes _site/
"""

from __future__ import annotations

import html
import re
import shutil
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
SITE = Path(__file__).resolve().parent
OUT = ROOT / "_site"
TUTORIALS = ROOT / "docs" / "tutorials"
REPO = "https://github.com/nickbrazeau/Vademecum"

# Pages from the repository that the site renders, beside the tutorials.
EXTRA = {
    "learning-science": ROOT / "docs" / "adr" / "0031-learner-model-and-what-to-study-next.md",
}


def tutorials() -> list[tuple[str, str, Path]]:
    """(slug, title, path) for each tutorial, in order."""
    found = []
    for path in sorted(TUTORIALS.glob("[0-9][0-9]-*.md")):
        title = path.read_text(encoding="utf-8").splitlines()[0].lstrip("# ").strip()
        found.append((path.stem, title, path))
    return found


def link_fix(body: str, slugs: set[str]) -> str:
    """Links between Markdown files become links between pages; anything else in the
    repository opens on GitHub."""

    def fix(match: re.Match[str]) -> str:
        target = match.group(1)
        if target.startswith(("http://", "https://", "#", "mailto:")):
            return match.group(0)
        name = Path(target.split("#")[0]).name
        stem = name[:-3] if name.endswith(".md") else None
        if stem in slugs:
            return f'href="{stem}.html"'
        if stem and stem.startswith("0031-"):
            return 'href="learning-science.html"'
        clean = re.sub(r"^(\.\./)+", "", target)
        prefix = "docs/tutorials/" if not target.startswith("..") else ("docs/" if target.startswith("../") and not target.startswith("../../") else "")
        return f'href="{REPO}/blob/main/{prefix}{clean}"'

    return re.sub(r'href="([^"]+)"', fix, body)


def page(title: str, body: str, *, nav: str, description: str = "") -> str:
    template = (SITE / "page.html").read_text(encoding="utf-8")
    return (
        template.replace("{{title}}", html.escape(title))
        .replace("{{description}}", html.escape(description or "Vademecum: a personal tutor for clinicians, for the minutes you have."))
        .replace("{{nav}}", nav)
        .replace("{{body}}", body)
        .replace("{{repo}}", REPO)
    )


def build() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    shutil.copy(SITE / "style.css", OUT / "style.css")
    shutil.copy(ROOT / "apps" / "web" / "public" / "icon.svg", OUT / "icon.svg")
    (OUT / ".nojekyll").write_text("")

    found = tutorials()
    slugs = {slug for slug, _title, _path in found}
    nav = "".join(f'<li><a href="{slug}.html">{html.escape(title)}</a></li>' for slug, title, _path in found)
    nav += '<li><a href="learning-science.html">The learning science</a></li>'

    landing = (SITE / "index.html").read_text(encoding="utf-8")
    cards = "".join(
        f'<a class="tutorial-card" href="{slug}.html"><span class="num">{i}</span><span>{html.escape(re.sub(r"^\d+\.\s*", "", title))}</span></a>'
        for i, (slug, title, _path) in enumerate(found, start=1)
    )
    (OUT / "index.html").write_text(page("Vademecum", landing.replace("{{tutorials}}", cards), nav=nav), encoding="utf-8")

    renderer = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists", "toc"])
    for index, (slug, title, path) in enumerate(found):
        body = renderer.reset().convert(path.read_text(encoding="utf-8"))
        prev = found[index - 1] if index > 0 else None
        nxt = found[index + 1] if index + 1 < len(found) else None
        pager = '<nav class="pager">'
        pager += f'<a href="{prev[0]}.html">← {html.escape(prev[1])}</a>' if prev else "<span></span>"
        pager += f'<a href="{nxt[0]}.html">{html.escape(nxt[1])} →</a>' if nxt else "<span></span>"
        pager += "</nav>"
        article = f'<article class="prose">{link_fix(body, slugs)}</article>{pager}'
        (OUT / f"{slug}.html").write_text(page(f"{title} · Vademecum", article, nav=nav), encoding="utf-8")

    for slug, path in EXTRA.items():
        body = renderer.reset().convert(path.read_text(encoding="utf-8"))
        (OUT / f"{slug}.html").write_text(
            page("The learning science · Vademecum", f'<article class="prose">{link_fix(body, slugs)}</article>', nav=nav), encoding="utf-8"
        )
    print(f"wrote {OUT} ({len(list(OUT.iterdir()))} files)")


if __name__ == "__main__":
    build()
