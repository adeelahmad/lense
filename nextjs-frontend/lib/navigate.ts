/** Leaving for another page with a full load, in a module of its own so tests can stand in for it (jsdom's
 * window.location can't be replaced). */
export const navigate = {
  assign: (url: string) => window.location.assign(url),
  replace: (url: string) => window.location.replace(url),
};
