/**
 * Rights statements the backend accepts (metadata.RIGHTS_RX): Creative Commons licences and public-domain tools, and
 * RightsStatements.org statements. Values are the canonical http:// URIs IIIF expects.
 */
export type RightsOption = { uri: string; code: string; name: string; group: "Creative Commons" | "RightsStatements.org" };

const cc = (path: string, code: string, name: string): RightsOption => ({
  uri: `http://creativecommons.org/${path}`,
  code,
  name,
  group: "Creative Commons",
});
const rs = (id: string, name: string): RightsOption => ({ uri: `http://rightsstatements.org/vocab/${id}/1.0/`, code: id, name, group: "RightsStatements.org" });

export const RIGHTS: RightsOption[] = [
  cc("licenses/by/4.0/", "CC BY 4.0", "Attribution 4.0"),
  cc("licenses/by-sa/4.0/", "CC BY-SA 4.0", "Attribution-ShareAlike 4.0"),
  cc("licenses/by-nc/4.0/", "CC BY-NC 4.0", "Attribution-NonCommercial 4.0"),
  cc("licenses/by-nc-sa/4.0/", "CC BY-NC-SA 4.0", "Attribution-NonCommercial-ShareAlike 4.0"),
  cc("licenses/by-nd/4.0/", "CC BY-ND 4.0", "Attribution-NoDerivatives 4.0"),
  cc("licenses/by-nc-nd/4.0/", "CC BY-NC-ND 4.0", "Attribution-NonCommercial-NoDerivatives 4.0"),
  cc("publicdomain/zero/1.0/", "CC0 1.0", "Public domain dedication"),
  cc("publicdomain/mark/1.0/", "PDM 1.0", "Public domain mark"),
  rs("InC", "In Copyright"),
  rs("InC-EDU", "In Copyright – educational use permitted"),
  rs("InC-NC", "In Copyright – non-commercial use permitted"),
  rs("InC-RUU", "In Copyright – rights-holder(s) unlocatable"),
  rs("NoC-US", "No Copyright – United States"),
  rs("NoC-NC", "No Copyright – non-commercial use only"),
  rs("NoC-OKLR", "No Copyright – other known legal restrictions"),
  rs("NKC", "No Known Copyright"),
  rs("CNE", "Copyright Not Evaluated"),
  rs("UND", "Copyright Undetermined"),
];

export const RIGHTS_RX = /^https?:\/\/(creativecommons\.org\/(licenses|publicdomain)\/|rightsstatements\.org\/vocab\/)/;

/** The canonical form the backend stores (https → http). */
export function canonicalRights(uri: string): string {
  return uri.trim().replace(/^https:\/\//, "http://");
}

/** A known statement for a URI (http or https, with or without the trailing slash). */
export function rightsFor(uri: string | null | undefined): RightsOption | undefined {
  if (!uri) return undefined;
  const u = canonicalRights(uri).replace(/\/?$/, "/");
  return RIGHTS.find((r) => r.uri === u);
}

/** "CC BY-NC 4.0" for a known URI, the URI itself otherwise, or "none". */
export function rightsShort(uri: string | null | undefined): string {
  if (!uri) return "none";
  return rightsFor(uri)?.code ?? uri;
}
