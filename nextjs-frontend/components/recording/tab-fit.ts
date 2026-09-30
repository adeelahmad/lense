/**
 * Priority+ tabs: which panel tabs fit on one row beside the More (…) button. Tabs keep their order and the ones that
 * don't fit move into the menu, with the `extra` tabs that always live there. The active tab is always on show: an
 * active menu tab joins the end of the row until another tab is picked.
 */
export function fitTabs(
  tabs: string[],
  extra: string[],
  width: (v: string) => number,
  avail: number,
  moreW: number,
  active: string,
): { shown: string[]; overflow: string[] } {
  const total = tabs.reduce((s, v) => s + width(v), 0);
  if (!extra.length && total <= avail) return { shown: tabs, overflow: [] };
  const activeExtra = extra.includes(active) ? active : null;
  const hasActive = activeExtra != null || tabs.includes(active);
  let used = moreW + (hasActive ? width(active) : 0);
  const fits = new Set<string>();
  for (const v of tabs) {
    if (v === active) continue;
    const w = width(v);
    if (used + w > avail) break;
    used += w;
    fits.add(v);
  }
  const shown = tabs.filter((v) => v === active || fits.has(v));
  if (activeExtra) shown.push(activeExtra);
  return { shown, overflow: [...tabs.filter((v) => !shown.includes(v)), ...extra.filter((v) => v !== activeExtra)] };
}
