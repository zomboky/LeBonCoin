"""Interface en ligne de commande.

Sous-commandes :
  deals     chercher puis classer par potentiel de pépite
  search    recherche simple, sortie compacte
  show      détail d'une annonce
  watch     uniquement ce qui est apparu depuis le dernier passage
  domains   lister/inspecter les packs thématiques
  seen      gérer la mémoire des annonces déjà vues
  research  vérifier un modèle/marque sur des pages web externes (ex-Mach2)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import cache, config, domains as domains_mod, render, research as research_mod
from .api import SearchParams, fetch, fetch_many
from .models import Listing
from .score import rank
from .transport import Blocked, Transport, TransportError


# --- Options partagées --------------------------------------------------------


def _add_search_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("query", nargs="?", default="", help="texte recherché")
    parser.add_argument("--domain", "-d", help="pack thématique (cf. `golddigger02 domains`)")
    parser.add_argument("--category", "-c", help="catégorie leboncoin (nom ou id)")
    parser.add_argument("--min-price", type=int)
    parser.add_argument("--max-price", type=int)
    parser.add_argument("--where", "-w", help="département, ville ou région")
    parser.add_argument("--radius", type=int, help="rayon en km autour de --where")
    parser.add_argument(
        "--sort",
        default="date",
        choices=["date", "recent", "price", "price_desc", "relevance"],
    )
    parser.add_argument("--seller", choices=["private", "pro"])
    parser.add_argument("--pages", "-p", type=int, default=2, help="pages par requête")
    parser.add_argument("--delay", type=float, default=None, help="pause entre requêtes (s)")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--no-browser", action="store_true", help="pas de repli navigateur")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument(
        "--from-fixture", help="lire un JSON local au lieu du réseau (tests)"
    )


def _add_ranking_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--top", "-n", type=int, default=20, help="annonces affichées")
    parser.add_argument("--min-score", type=float, default=0.0)
    parser.add_argument("--min-cohort", type=int, default=None)
    parser.add_argument(
        "--format",
        "-f",
        default="tsv",
        choices=["tsv", "jsonl", "ids", "vision"],
        help="vision = images des finalistes, pour inspection visuelle",
    )
    parser.add_argument(
        "--unseen", action="store_true", help="exclure les annonces déjà vues"
    )
    parser.add_argument(
        "--no-mark", action="store_true", help="ne pas mémoriser les annonces affichées"
    )


def _params_from_args(args) -> SearchParams:
    return SearchParams(
        text=args.query or "",
        category=args.category,
        min_price=args.min_price,
        max_price=args.max_price,
        where=args.where,
        radius_km=args.radius,
        sort=args.sort,
        seller=args.seller,
        pages=args.pages,
    )


def _load_fixture(path: str) -> list[Listing]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    ads = data.get("ads") if isinstance(data, dict) else data
    return [Listing.from_raw(raw) for raw in ads or [] if isinstance(raw, dict)]


def _collect(args, store) -> tuple[list[Listing], object]:
    """Récupère les annonces (réseau ou fixture) et le pack éventuel."""
    domain = domains_mod.get(args.domain) if args.domain else None
    if args.domain and domain is None:
        available = ", ".join(sorted(domains_mod.load_all())) or "(aucun)"
        raise SystemExit(f"golddigger02: pack « {args.domain} » inconnu. Disponibles : {available}")

    if args.from_fixture:
        return _load_fixture(args.from_fixture), domain

    transport = Transport(
        delay=args.delay, use_browser=not args.no_browser, verbose=args.verbose
    )
    params = _params_from_args(args)
    use_cache = not args.no_cache

    # Un pack sans requête explicite lance ses propres requêtes-sources.
    if domain is not None and not args.query and domain.queries:
        if not args.category and domain.category_ids:
            params.category = domain.category_ids[0]
        on_query = (lambda q: print(f"golddigger02: → {q}", file=sys.stderr)) if args.verbose else None
        listings = fetch_many(domain.queries, params, transport, store, use_cache, on_query)
    else:
        if domain is not None and not args.category and domain.category_ids:
            params.category = domain.category_ids[0]
        listings = fetch(params, transport, store, use_cache)

    return listings, domain


# --- Commandes ----------------------------------------------------------------


def cmd_deals(args) -> int:
    with cache.Store() as store:
        try:
            listings, domain = _collect(args, store)
        except Blocked as exc:
            print(
                f"golddigger02: leboncoin a bloqué la requête ({exc}).\n"
                "     Depuis une IP de datacenter c'est attendu. Réessayer depuis une\n"
                "     connexion résidentielle, ou installer playwright pour le repli.",
                file=sys.stderr,
            )
            return 2
        except TransportError as exc:
            print(f"golddigger02: {exc}", file=sys.stderr)
            return 2

        total = len(listings)
        if args.unseen:
            listings = store.filter_unseen(listings)

        ranked = rank(
            listings,
            domain=domain,
            min_cohort=args.min_cohort,
            min_score=args.min_score,
            top=args.top,
        )

        print(render.summary_line(total, len(ranked), args.domain or "", args.unseen))
        if ranked:
            print(render.render(ranked, args.format))
            if not args.no_mark:
                store.mark_seen(ranked, args.domain or "")
        return 0


def cmd_search(args) -> int:
    with cache.Store() as store:
        try:
            listings, _ = _collect(args, store)
        except (Blocked, TransportError) as exc:
            print(f"golddigger02: {exc}", file=sys.stderr)
            return 2
        subset = listings[: args.top]
        print(render.summary_line(len(listings), len(subset)))
        if subset:
            print(render.as_tsv(subset, with_reasons=False))
        return 0


def cmd_watch(args) -> int:
    args.unseen = True
    return cmd_deals(args)


def cmd_show(args) -> int:
    transport = Transport(use_browser=not args.no_browser, verbose=args.verbose)
    try:
        raw = transport.get_ad(args.ad_id)
    except (Blocked, TransportError) as exc:
        print(f"golddigger02: {exc}", file=sys.stderr)
        return 2
    listing = Listing.from_raw(raw)
    domain = domains_mod.get(args.domain) if args.domain else None
    rank([listing], domain=domain)
    print(render.as_detail(listing))
    return 0


def cmd_domains(args) -> int:
    all_domains = domains_mod.load_all()
    if not all_domains:
        print("golddigger02: aucun pack trouvé.")
        return 1

    if args.name:
        domain = all_domains.get(args.name.lower())
        if domain is None:
            print(f"golddigger02: pack « {args.name} » inconnu.", file=sys.stderr)
            return 1
        print(f"{domain.name} — {domain.label}")
        print(f"  catégories : {', '.join(domain.categories) or '-'}")
        print(f"  requêtes   : {len(domain.queries)}")
        for query in domain.queries:
            print(f"    · {query}")
        print(f"  marques    : {', '.join(domain.brands[:20]) or '-'}")
        print(f"  modèles    : {len(domain.models)} règles")
        print(f"  origine    : {', '.join(domain.origin_markers[:10]) or '-'}")
        print(f"  repro      : {', '.join(domain.repro_markers[:10]) or '-'}")
        print(f"  barèmes    : {len(domain.value_bands)}")
        return 0

    for name in sorted(all_domains):
        domain = all_domains[name]
        print(f"{name:18} {len(domain.queries):>3} requêtes  {domain.label}")
    return 0


def cmd_research(args) -> int:
    """Vérifier un modèle/marque identifié sur des pages web externes.

    Ne touche pas au pipeline leboncoin : sert à confirmer, en dehors, qu'une
    référence obscure remontée par `hidden_model`/`typo_brand` est bien rare ou
    recherchée, avant de faire confiance au score. Ex-Mach2 (scrape/batch),
    branché sur le cache SQLite de l'outil plutôt qu'un cache fichier séparé.
    """
    with cache.Store() as store:
        results, out_dir = research_mod.run(
            args.urls,
            store=store,
            render=args.render,
            query=args.filter,
            max_chars=args.max_chars,
            concurrency=args.concurrency,
            use_cache=not args.no_cache,
        )
    print(research_mod.summary_lines(results, out_dir))
    if args.show:
        first_ok = next((r for r in results if not r.error), None)
        if first_ok:
            print(f"\n--- aperçu ({args.show} car.) : {first_ok.url} ---")
            print(first_ok.markdown[: args.show])
    return 0 if any(not r.error for r in results) else 1


def cmd_seen(args) -> int:
    with cache.Store() as store:
        if args.forget or args.all or args.forget_domain:
            removed = store.forget(
                ad_ids=args.forget or None,
                domain=args.forget_domain,
            )
            print(f"golddigger02: {removed} annonces oubliées.")
            return 0
        stats = store.seen_stats()
        if not stats:
            print("golddigger02: mémoire vide.")
            return 0
        for row in stats:
            print(f"{row['domain']:18} {row['count']:>6}  dernier: {row['last_seen']}")
        return 0


# --- Point d'entrée -----------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="golddigger02",
        description="Recherche leboncoin et détection de pépites.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    deals = sub.add_parser("deals", help="chercher et classer par potentiel de pépite")
    _add_search_options(deals)
    _add_ranking_options(deals)
    deals.set_defaults(func=cmd_deals)

    search = sub.add_parser("search", help="recherche simple, sortie compacte")
    _add_search_options(search)
    search.add_argument("--top", "-n", type=int, default=25)
    search.set_defaults(func=cmd_search)

    watch = sub.add_parser("watch", help="uniquement les nouveautés depuis le dernier run")
    _add_search_options(watch)
    _add_ranking_options(watch)
    watch.set_defaults(func=cmd_watch)

    show = sub.add_parser("show", help="détail d'une annonce")
    show.add_argument("ad_id")
    show.add_argument("--domain", "-d")
    show.add_argument("--no-browser", action="store_true")
    show.add_argument("--verbose", "-v", action="store_true")
    show.set_defaults(func=cmd_show)

    domains_cmd = sub.add_parser("domains", help="lister les packs thématiques")
    domains_cmd.add_argument("name", nargs="?")
    domains_cmd.set_defaults(func=cmd_domains)

    research = sub.add_parser(
        "research",
        help="vérifier un modèle/marque sur des pages web externes (ex-Mach2)",
    )
    research.add_argument("urls", nargs="+", help="une ou plusieurs URLs à inspecter")
    research.add_argument("--filter", help="ne garder que les passages pertinents à cette requête")
    research.add_argument("--max-chars", type=int, help="plafond de caractères par page")
    research.add_argument("--render", action="store_true", help="rendu JS (Playwright), sites SPA")
    research.add_argument("--concurrency", type=int, default=5)
    research.add_argument("--no-cache", action="store_true")
    research.add_argument("--show", type=int, default=0, help="aperçu console (N car. de la 1ère page)")
    research.set_defaults(func=cmd_research)

    seen = sub.add_parser("seen", help="mémoire des annonces déjà vues")
    seen.add_argument("--forget", nargs="*", help="identifiants à oublier")
    seen.add_argument("--all", action="store_true", help="tout oublier")
    seen.add_argument("--forget-domain", help="oublier un pack entier")
    seen.set_defaults(func=cmd_seen)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config.ensure_dirs()
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0
