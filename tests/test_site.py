"""Checks on the static site in `site/`, which Cloudflare Pages serves as-is.

They guard what the host will not tell you until a deploy or a visitor finds it:
a link to a file that is not there, a file over Pages' per-file limit, and a
stray reference to the code host that the site is meant not to show.
"""

from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import pytest

SITE = Path(__file__).resolve().parent.parent / "site"

# Cloudflare Pages rejects a deploy containing any single file larger than this.
PAGES_MAX_FILE_BYTES = 25 * 1024 * 1024


class _RefCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.refs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name in ("href", "src") and value:
                self.refs.append(value)


def collect_refs(html: str) -> list[str]:
    """Every `href` and `src` value in the document, in order."""
    parser = _RefCollector()
    parser.feed(html)
    return parser.refs


def local_target(ref: str, page: Path, root: Path) -> Path | None:
    """The file a reference resolves to inside `root`, or None if it is not local.

    External URLs, `mailto:`-style schemes and same-page `#anchors` are not local.
    A root-relative path (`/style.css`) resolves against `root`; anything else
    against the page's directory. A path ending in `/` means its `index.html`.
    """
    parts = urlsplit(ref)
    if parts.scheme or parts.netloc or not parts.path:
        return None
    base = root if parts.path.startswith("/") else page.parent
    target = base / parts.path.lstrip("/")
    if parts.path.endswith("/"):
        target = target / "index.html"
    return target


def broken_refs(page: Path, root: Path) -> list[str]:
    """References on `page` whose local target does not exist as a file."""
    broken = []
    for ref in collect_refs(page.read_text(encoding="utf-8")):
        target = local_target(ref, page, root)
        if target is not None and not target.is_file():
            broken.append(ref)
    return broken


def oversized_files(root: Path, limit: int) -> list[Path]:
    """Files under `root` larger than `limit` bytes."""
    return [p for p in root.rglob("*") if p.is_file() and p.stat().st_size > limit]


# --- helper behaviour -------------------------------------------------------


def test_collect_refs_finds_href_and_src():
    html = '<a href="a.html">x</a><img src="b.png"><link rel="x" href="c.css">'
    assert collect_refs(html) == ["a.html", "b.png", "c.css"]


def test_collect_refs_skips_empty_and_valueless_attributes():
    assert collect_refs('<a href="">x</a><a href>y</a><p>z</p>') == []


@pytest.mark.parametrize(
    "ref",
    ["https://example.com/x", "//cdn.example.com/x.js", "mailto:me@example.com", "#games"],
)
def test_local_target_ignores_non_local_refs(tmp_path: Path, ref: str):
    assert local_target(ref, tmp_path / "index.html", tmp_path) is None


def test_local_target_resolves_relative_to_page(tmp_path: Path):
    page = tmp_path / "games" / "index.html"
    assert local_target("play.js", page, tmp_path) == tmp_path / "games" / "play.js"


def test_local_target_resolves_root_relative_to_root(tmp_path: Path):
    page = tmp_path / "games" / "index.html"
    assert local_target("/style.css", page, tmp_path) == tmp_path / "style.css"


def test_local_target_drops_query_and_fragment(tmp_path: Path):
    page = tmp_path / "index.html"
    assert local_target("style.css?v=2#x", page, tmp_path) == tmp_path / "style.css"


def test_local_target_directory_means_index(tmp_path: Path):
    page = tmp_path / "index.html"
    assert local_target("/", page, tmp_path) == tmp_path / "index.html"
    assert local_target("games/", page, tmp_path) == tmp_path / "games" / "index.html"


def test_broken_refs_reports_only_missing_files(tmp_path: Path):
    (tmp_path / "style.css").write_text("", encoding="utf-8")
    page = tmp_path / "index.html"
    page.write_text(
        '<link href="style.css"><a href="missing.exe">d</a><a href="https://x.test">e</a>',
        encoding="utf-8",
    )
    assert broken_refs(page, tmp_path) == ["missing.exe"]


def test_oversized_files_uses_strict_limit(tmp_path: Path):
    (tmp_path / "at.bin").write_bytes(b"x" * 10)
    (tmp_path / "over.bin").write_bytes(b"x" * 11)
    assert oversized_files(tmp_path, 10) == [tmp_path / "over.bin"]


# --- the real site ----------------------------------------------------------


def test_site_has_home_and_404_pages():
    assert (SITE / "index.html").is_file()
    assert (SITE / "404.html").is_file()


@pytest.mark.parametrize("page", sorted(SITE.rglob("*.html")), ids=lambda p: p.name)
def test_site_page_has_no_broken_local_links(page: Path):
    assert broken_refs(page, SITE) == []


def test_site_files_fit_cloudflare_pages_limit():
    assert oversized_files(SITE, PAGES_MAX_FILE_BYTES) == []


def test_site_does_not_mention_github():
    # The site is meant to be indistinguishable from a self-run one.
    offenders = [
        p.name
        for p in SITE.rglob("*")
        if p.is_file()
        and p.suffix in {".html", ".css", ".js"}
        and "github" in p.read_text(encoding="utf-8").lower()
    ]
    assert offenders == []
