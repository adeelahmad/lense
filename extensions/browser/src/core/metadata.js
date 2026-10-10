// A page's metadata, from its <meta> tags, Open Graph, JSON-LD and the document itself.

const meta = (doc, ...names) => {
  for (const n of names) {
    const el = doc.querySelector(`meta[property="${n}"], meta[name="${n}"], meta[itemprop="${n}"]`);
    const v = el?.getAttribute("content")?.trim();
    if (v) return v;
  }
  return "";
};

function jsonLd(doc) {
  const found = [];
  for (const s of doc.querySelectorAll('script[type="application/ld+json"]')) {
    try {
      const data = JSON.parse(s.textContent);
      const stack = [data];
      while (stack.length) {
        const x = stack.pop();
        if (Array.isArray(x)) stack.push(...x);
        else if (x && typeof x === "object") {
          if (x["@graph"]) stack.push(x["@graph"]);
          found.push(x);
        }
      }
    } catch {
      // a broken JSON-LD block says nothing
    }
  }
  const article = found.find((x) => /Article|BlogPosting|NewsArticle|WebPage|Report/.test([].concat(x["@type"] ?? []).join(" ")));
  if (!article) return {};
  const name = (a) => (typeof a === "string" ? a : a?.name ?? "");
  const authors = [].concat(article.author ?? []).map(name).filter(Boolean);
  const image = [].concat(article.image ?? [])[0];
  return {
    author: authors.join(", "),
    published: article.datePublished ?? "",
    modified: article.dateModified ?? "",
    headline: article.headline ?? "",
    image: typeof image === "string" ? image : image?.url ?? "",
    keywords: [].concat(article.keywords ?? []).flatMap((k) => String(k).split(",")),
  };
}

const absolute = (href, base) => {
  if (!href) return "";
  try {
    return new URL(href, base).href;
  } catch {
    return "";
  }
};

const isoDate = (s) => {
  if (!s) return "";
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? "" : d.toISOString();
};

/** Everything templates can use about the page itself (not the clipped content). */
export function extractMetadata(doc, pageUrl = doc.URL) {
  const ld = jsonLd(doc);
  const canonical = doc.querySelector('link[rel="canonical"]')?.getAttribute("href");
  const url = absolute(canonical, pageUrl) || pageUrl;
  let host = "";
  let origin = "";
  try {
    ({ host, origin } = new URL(url));
  } catch {
    // about: and file: pages have no host
  }
  const keywords = [
    ...meta(doc, "keywords", "news_keywords").split(","),
    ...[...doc.querySelectorAll('meta[property="article:tag"]')].map((m) => m.getAttribute("content") ?? ""),
    ...(ld.keywords ?? []),
  ]
    .map((k) => k.trim())
    .filter(Boolean);
  return {
    title: meta(doc, "og:title", "twitter:title") || ld.headline || doc.title?.trim() || "",
    url,
    domain: host.replace(/^www\./, ""),
    origin,
    siteName: meta(doc, "og:site_name", "application-name") || host.replace(/^www\./, ""),
    byline: meta(doc, "author", "article:author", "parsely-author", "sailthru.author") || ld.author || "",
    excerpt: meta(doc, "description", "og:description", "twitter:description"),
    published: isoDate(
      meta(doc, "article:published_time", "datePublished", "date", "DC.date.issued", "dcterms.created") ||
        ld.published ||
        doc.querySelector("time[datetime]")?.getAttribute("datetime"),
    ),
    modified: isoDate(meta(doc, "article:modified_time", "dateModified", "dcterms.modified") || ld.modified),
    image: absolute(meta(doc, "og:image", "og:image:url", "twitter:image") || ld.image, pageUrl),
    keywords: [...new Set(keywords)],
    lang: doc.documentElement?.getAttribute("lang") || meta(doc, "og:locale") || "",
  };
}
