"""Recherche externe de vérification — HTML quelconque → markdown propre en fichiers.

Ce module est un portage direct du cœur de **Mach2** (github.com/zomboky/mach2,
un clone local de Firecrawl) : `trafilatura` isole le contenu principal d'une
page et le convertit en markdown, le résultat est écrit sur disque, et seul un
résumé compact remonte à l'appelant. Repli navigateur (Playwright) pour les
sites qui rendent leur contenu en JavaScript.

Rôle dans GoldDigger02 : le scoring (`score.py`) travaille sur des annonces
leboncoin, dont les données sont du JSON structuré — ce module n'y touche pas.
Il sert à l'étape suivante, volontairement séparée : quand un signal
`hidden_model`/`typo_brand` fait remonter une référence obscure, vérifier sa
rareté ou sa valeur sur une page web ordinaire (forum de collectionneurs,
résultat d'enchères, fiche technique) sans jamais faire transiter la page
entière par le contexte de l'agent.

Différences volontaires avec Mach2 : seuls `scrape`/`batch` sont repris — `map`
(découverte d'URLs d'un site) et `crawl` (aspiration récursive) ne servent à
rien pour vérifier un objet précis et auraient ajouté du poids sans usage réel
ici. Le cache disque de Mach2 (fichiers JSON individuels) est remplacé par la
table `responses` déjà utilisée pour l'API leboncoin, pour n'avoir qu'un seul
mécanisme de persistance dans l'outil.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from . import cache, config

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 GoldDigger02/0.2"
)
DEFAULT_TIMEOUT = 30
DEFAULT_TTL = 24 * 3600  # une page de référence bouge rarement en un jour

_WORD_RX = re.compile(r"[\wàâäéèêëîïôöùûüç]+", re.IGNORECASE | re.UNICODE)


# --- Récupération --------------------------------------------------------------


def _headers(extra: dict | None = None) -> dict:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fr,en;q=0.8",
    }
    if extra:
        headers.update(extra)
    return headers


def fetch_static(url: str, timeout: int = DEFAULT_TIMEOUT, headers: dict | None = None) -> dict:
    result = {
        "url": url, "final_url": url, "status": None, "content_type": None,
        "html": "", "render": False, "error": None,
    }
    try:
        response = requests.get(
            url, headers=_headers(headers), timeout=timeout, allow_redirects=True
        )
        result["status"] = response.status_code
        result["final_url"] = response.url
        result["content_type"] = response.headers.get("Content-Type", "").lower()
        response.encoding = response.encoding or response.apparent_encoding
        result["html"] = response.text
    except requests.RequestException as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def fetch_render(
    url: str,
    timeout: int = DEFAULT_TIMEOUT,
    wait_for: str | None = None,
    headers: dict | None = None,
) -> dict:
    """Rendu JavaScript via Playwright — pour les sites qui affichent leur
    contenu réel côté client (SPA)."""
    result = {
        "url": url, "final_url": url, "status": None, "content_type": "text/html",
        "html": "", "render": True, "error": None,
    }
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        result["error"] = "playwright absent — `pip install playwright && playwright install chromium`"
        return result

    kwargs: dict = {"headless": True}
    if config.CHROMIUM_PATH and Path(config.CHROMIUM_PATH).exists():
        kwargs["executable_path"] = config.CHROMIUM_PATH
    if config.PROXY:
        kwargs["proxy"] = {"server": config.PROXY}

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**kwargs)
            try:
                page = browser.new_page(user_agent=USER_AGENT, extra_http_headers=headers or {})
                response = page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
                if response is not None:
                    result["status"] = response.status
                    result["final_url"] = response.url
                if wait_for:
                    try:
                        if wait_for.isdigit():
                            page.wait_for_timeout(int(wait_for))
                        else:
                            page.wait_for_selector(wait_for, timeout=timeout * 1000)
                    except Exception:
                        pass  # best-effort : on renvoie ce qu'on a
                result["html"] = page.content()
            finally:
                browser.close()
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    return result


def fetch(
    url: str,
    store: "cache.Store | None" = None,
    render: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
    wait_for: str | None = None,
    headers: dict | None = None,
    use_cache: bool = True,
    ttl: int = DEFAULT_TTL,
) -> dict:
    """Point d'entrée unifié : cache → fetch (statique ou rendu) → mise en cache.

    Réutilise la table `responses` du cache SQLite de l'outil plutôt qu'un cache
    fichier séparé — un seul mécanisme de persistance pour tout GoldDigger02.
    """
    key = cache.cache_key("research", url, render)
    if use_cache and store is not None:
        cached = store.get_response(key, ttl)
        if cached is not None:
            return {**cached, "from_cache": True}

    result = fetch_render(url, timeout, wait_for, headers) if render else fetch_static(url, timeout, headers)
    result["from_cache"] = False

    if use_cache and store is not None and result["html"] and not result["error"]:
        store.put_response(key, result)
    return result


# --- Extraction ------------------------------------------------------------


def normalize_url(url: str) -> str:
    url = urldefrag(url)[0]
    parsed = urlparse(url)
    path = parsed.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urlunparse((parsed.scheme, parsed.netloc.lower(), path, parsed.params, parsed.query, ""))


def to_markdown(html: str, url: str, only_main: bool = True) -> str:
    """HTML → markdown propre. `trafilatura` isole le contenu principal ; repli
    bs4 + markdownify si `trafilatura` ne trouve rien (pages atypiques)."""
    if not html:
        return ""

    import trafilatura

    markdown = trafilatura.extract(
        html,
        output_format="markdown",
        include_links=True,
        include_tables=True,
        include_images=False,
        favor_recall=not only_main,
        with_metadata=False,
        url=url,
    )
    if markdown and markdown.strip():
        return markdown.strip()
    return _fallback_markdown(html, only_main)


def _fallback_markdown(html: str, only_main: bool = True) -> str:
    try:
        from markdownify import markdownify as mdify
    except ImportError:
        soup = BeautifulSoup(html, "lxml")
        return soup.get_text("\n", strip=True)

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    if only_main:
        for tag in soup(["nav", "header", "footer", "aside", "form"]):
            tag.decompose()
    main = soup.find("main") or soup.find("article") or soup.body or soup
    return mdify(str(main), heading_style="ATX", strip=["img"]).strip()


def extract_metadata(html: str, url: str, fetch_meta: dict | None = None) -> dict:
    import trafilatura

    meta: dict = {"sourceURL": url}
    if fetch_meta:
        meta["statusCode"] = fetch_meta.get("status")
        meta["contentType"] = fetch_meta.get("content_type")
        if fetch_meta.get("final_url") and fetch_meta["final_url"] != url:
            meta["finalURL"] = fetch_meta["final_url"]

    try:
        tmeta = trafilatura.extract_metadata(html, default_url=url)
        if tmeta:
            data = tmeta.as_dict() if hasattr(tmeta, "as_dict") else {}
            for key in ("title", "author", "date", "sitename", "description"):
                if data.get(key):
                    meta[key] = data[key]
    except Exception:
        pass

    try:
        soup = BeautifulSoup(html, "lxml")
        if soup.title and soup.title.string and soup.title.string.strip():
            meta["title"] = soup.title.string.strip()
        html_tag = soup.find("html")
        if html_tag and html_tag.get("lang"):
            meta["language"] = html_tag["lang"]
    except Exception:
        pass
    return meta


def extract_links(html: str, base_url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    base_host = urlparse(base_url).netloc
    internal: list[str] = []
    external: list[str] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urldefrag(urljoin(base_url, href))[0]
        if urlparse(absolute).scheme not in ("http", "https") or absolute in seen:
            continue
        seen.add(absolute)
        (internal if urlparse(absolute).netloc == base_host else external).append(absolute)

    return {"internal": internal, "external": external}


# --- Filtrage par pertinence -----------------------------------------------


def _tokens(text: str) -> list[str]:
    return [w.lower() for w in _WORD_RX.findall(text)]


def filter_markdown(markdown: str, query: str, max_blocks: int = 40) -> str:
    """Ne garde que les blocs pertinents pour `query`, dans l'ordre du document.

    C'est ce qui permet de vérifier « ce Gueneau 123 est-il rare ? » sans faire
    remonter la page entière d'un forum de 5000 mots.
    """
    if not markdown or not query:
        return markdown
    terms = set(_tokens(query))
    if not terms:
        return markdown

    blocks = [b for b in re.split(r"\n\s*\n", markdown) if b.strip()]
    scored = []
    for i, block in enumerate(blocks):
        toks = _tokens(block)
        if not toks:
            continue
        hits = sum(1 for t in toks if t in terms)
        score = hits / (len(toks) ** 0.5)
        if block.lstrip().startswith("#") and hits:
            score += 1.0
        if score > 0:
            scored.append((score, i, block))

    if not scored:
        return markdown  # rien ne matche → on ne masque pas tout

    scored.sort(key=lambda x: x[0], reverse=True)
    keep = sorted(i for _, i, _ in scored[:max_blocks])
    return "\n\n".join(blocks[i] for i in keep)


def truncate(markdown: str, max_chars: int | None) -> str:
    if not max_chars or len(markdown) <= max_chars:
        return markdown
    cut = markdown[:max_chars]
    nl = cut.rfind("\n\n")
    if nl > max_chars * 0.6:
        cut = cut[:nl]
    return cut.rstrip() + f"\n\n… [tronqué à {max_chars} caractères]"


# --- Orchestration -----------------------------------------------------------


@dataclass
class ResearchResult:
    url: str
    final_url: str = ""
    status: int | None = None
    title: str = ""
    markdown: str = ""
    chars: int = 0
    words: int = 0
    from_cache: bool = False
    error: str = ""
    path: str = ""


def research_one(
    url: str,
    store: "cache.Store | None" = None,
    render: bool = False,
    query: str | None = None,
    max_chars: int | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    use_cache: bool = True,
) -> ResearchResult:
    fetched = fetch(url, store, render=render, timeout=timeout, use_cache=use_cache)
    result = ResearchResult(url=url, final_url=fetched.get("final_url", url), status=fetched.get("status"))

    if fetched.get("error") or not fetched.get("html"):
        result.error = fetched.get("error") or "réponse vide"
        return result

    html = fetched["html"]
    meta = extract_metadata(html, url, fetched)
    markdown = to_markdown(html, url)
    if query:
        markdown = filter_markdown(markdown, query)
    if max_chars:
        markdown = truncate(markdown, max_chars)

    result.title = meta.get("title", "")
    result.markdown = markdown
    result.chars = len(markdown)
    result.words = len(markdown.split())
    result.from_cache = fetched.get("from_cache", False)
    return result


def research_many(
    urls: list[str],
    store: "cache.Store | None" = None,
    render: bool = False,
    query: str | None = None,
    max_chars: int | None = None,
    concurrency: int = 5,
    timeout: int = DEFAULT_TIMEOUT,
    use_cache: bool = True,
) -> list[ResearchResult]:
    """Récupère plusieurs URLs en parallèle. Chaque annonce leboncoin ne
    contribue qu'un ou deux termes de recherche externes ; le parallélisme
    évite qu'une page lente ne bloque les autres."""
    ordered = list(dict.fromkeys(u.strip() for u in urls if u.strip()))
    results: dict[str, ResearchResult] = {}

    def work(u: str) -> ResearchResult:
        return research_one(u, store, render, query, max_chars, timeout, use_cache)

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {pool.submit(work, u): u for u in ordered}
        for future in as_completed(futures):
            u = futures[future]
            try:
                results[u] = future.result()
            except Exception as exc:
                results[u] = ResearchResult(url=u, error=f"{type(exc).__name__}: {exc}")

    return [results[u] for u in ordered]


# --- Écriture sur disque ------------------------------------------------------


def default_out_dir(prefix: str = "research") -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = config.CACHE_DIR / "research" / f"{prefix}-{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    return out


def slug(url: str, max_len: int = 60) -> str:
    parsed = urlparse(url)
    raw = (parsed.netloc + parsed.path).strip("/") or parsed.netloc or "page"
    cleaned = re.sub(r"[^\w\-.]+", "_", raw).strip("_")
    return (cleaned[:max_len].rstrip("_") or "page")


def write_result(out_dir: Path, result: ResearchResult) -> Path | None:
    if result.error or not result.markdown:
        return None
    path = out_dir / (slug(result.url) + ".md")
    front_matter = f"---\ntitle: {result.title}\nsourceURL: {result.url}\n---\n\n"
    path.write_text(front_matter + result.markdown, encoding="utf-8")
    result.path = str(path)
    return path


def write_manifest(out_dir: Path, results: list[ResearchResult]) -> Path:
    import json

    entries = [
        {
            "url": r.url,
            "title": r.title,
            "status": r.status,
            "chars": r.chars,
            "words": r.words,
            "from_cache": r.from_cache,
            "file": r.path or None,
            "error": r.error or None,
        }
        for r in results
    ]
    path = out_dir / "manifest.json"
    path.write_text(
        json.dumps({"count": len(entries), "entries": entries}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def run(
    urls: list[str],
    store: "cache.Store | None" = None,
    render: bool = False,
    query: str | None = None,
    max_chars: int | None = None,
    concurrency: int = 5,
    use_cache: bool = True,
    out_dir: Path | None = None,
) -> tuple[list[ResearchResult], Path]:
    """Récupère, écrit les fichiers, renvoie les résultats + le dossier de sortie."""
    out_dir = out_dir or default_out_dir()
    results = research_many(
        urls, store, render=render, query=query, max_chars=max_chars,
        concurrency=concurrency, use_cache=use_cache,
    )
    for result in results:
        write_result(out_dir, result)
    write_manifest(out_dir, results)
    return results, out_dir


def summary_lines(results: list[ResearchResult], out_dir: Path) -> str:
    """Résumé compact : une ligne par URL, jamais le contenu complet."""
    lines = [f"# {len(results)} page(s) → {out_dir}"]
    for r in results:
        if r.error:
            lines.append(f"✗ {r.url} — {r.error}")
        else:
            tag = " [cache]" if r.from_cache else ""
            lines.append(f"✓ {r.title or '(sans titre)'} — {r.words} mots{tag} — {r.path}")
    return "\n".join(lines)
