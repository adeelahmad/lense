/** Wiki addresses: an entity's page is /wiki/<namespace>/<Name_As_Written>, as the API's `page` says (app/domain/wiki.py).
 * Spaces become underscores; any case, and a name it had before a rename or a merge, find it too. */
export function wikiSlug(name: string): string {
  return name.trim().replace(/\s+/g, "_");
}

/** The page of the entity called `name` in namespace `ns`: /wiki/work/Ada_Lovelace. */
export function wikiPath(ns: string, name: string): string {
  return `/wiki/${encodeURIComponent(ns)}/${encodeURIComponent(wikiSlug(name))}`;
}

function unescape(s: string): string {
  try {
    return decodeURIComponent(s);
  } catch {
    return s;
  }
}

/** The address a /wiki/<ns>/… route was opened with, as written: its parts joined (a name may hold a /), unescaped. */
export function addressOf(parts: string[]): string {
  return parts.map(unescape).join("/");
}

/** Whether two paths are the same page however they're escaped (/wiki/a/McDonald's and /wiki/a/McDonald%27s). */
export function samePath(a: string, b: string): boolean {
  return unescape(a) === unescape(b);
}

/** The name an address reads as, for the tab's title while the page loads: Ada_Lovelace -> Ada Lovelace. */
export function titleOf(address: string): string {
  return address.replace(/_+/g, " ").trim();
}
