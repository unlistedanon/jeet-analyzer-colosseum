"""Explicit public discovery surface; never infer public status from an SPA fallback."""

PUBLIC_ORIGIN = "https://jeet.example"
PUBLIC_PAGES = frozenset({"/", "/methodology/"})
PUBLIC_DISCOVERY = PUBLIC_PAGES | {"/robots.txt", "/sitemap.xml"}

ROBOTS = "\n\n".join(
    f"User-agent: {agent}\nAllow: /\nDisallow: /api/\nDisallow: /admin\nDisallow: /auth\nDisallow: /login\nDisallow: /reports/\nDisallow: /receipts/\nDisallow: /demo/\nDisallow: /*?admin=\nDisallow: /*&admin=\nDisallow: /*?demo=\nDisallow: /*&demo="
    for agent in ("Googlebot", "Bingbot", "OAI-SearchBot", "*")
) + f"\n\nSitemap: {PUBLIC_ORIGIN}/sitemap.xml\n"

SITEMAP = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "".join(
    f"  <url><loc>{PUBLIC_ORIGIN}{path}</loc></url>\n" for path in sorted(PUBLIC_PAGES)
) + "</urlset>\n"
