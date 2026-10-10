import { JSDOM } from "jsdom";

export const ARTICLE = `<!doctype html><html lang="en"><head>
<title>Fallback title | Example</title>
<meta property="og:title" content="How Lenses Work">
<meta property="og:site_name" content="Example Science">
<meta name="author" content="Ada Lovelace">
<meta name="description" content="A short tour of optics.">
<meta property="article:published_time" content="2026-03-04T10:00:00Z">
<meta name="keywords" content="optics, lenses">
<meta property="og:image" content="/img/lead.jpg">
<link rel="canonical" href="https://example.com/articles/lenses">
</head><body>
<nav><a href="/">Home</a> <a href="/about">About</a></nav>
<article>
<h1>How Lenses Work</h1>
<p>A lens bends light. This paragraph is long enough for Readability to think it is the article body, which it needs
to be, so here is some more text about refraction, focal lengths and the way glass slows light down as it passes.</p>
<p>See <a href="/glossary#focal">the glossary</a> for terms, and the <a href="javascript:alert(1)">bad link</a>.</p>
<img data-src="/img/diagram.png" src="data:image/gif;base64,R0lGOD" alt="A ray diagram">
<pre><code class="language-python">print("focus")
</code></pre>
<table><tr><th>Lens</th><th>Focal</th></tr><tr><td>A</td><td>50mm</td></tr></table>
<p>Another paragraph with <em>emphasis</em> and <strong>strength</strong>, again long enough to count as content for
the extraction step that keeps articles and drops the rest of the page.</p>
<script>window.evil = 1</script>
</article>
<footer>Copyright footer</footer>
</body></html>`;

export function page(html = ARTICLE, url = "https://example.com/articles/lenses?ref=x") {
  return new JSDOM(html, { url }).window.document;
}
