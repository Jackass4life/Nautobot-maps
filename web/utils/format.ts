/**
 * "2026-11-05 09:57 UTC": the same text on the server and in the browser, so
 * hydration never differs (toLocaleString() depends on each side's locale and zone).
 */
export function formatUtc(iso: string): string {
  return `${new Date(iso).toISOString().slice(0, 16).replace('T', ' ')} UTC`
}
