// The app's own sign-in (#717): where a page goes to sign in, and where it may go back.

/** /auth?redirect=<this page>: the API answers a 401 with `sign_in` when AD sign-in is on. */
export function signInAddress(page: string, here: string): string {
  return `${page}?redirect=${encodeURIComponent(here)}`;
}

/** *value* when it is a path on this site, else "/": never another site
 * ("//evil.example", "/\\evil.example", "https://…"), so a link can't send someone
 * signing in elsewhere. */
export function safeRedirect(value: unknown): string {
  if (typeof value !== "string" || !value.startsWith("/")) return "/";
  if (value.startsWith("//") || value.startsWith("/\\")) return "/";
  // A control character could hide either of those from the check above.
  if ([...value].some((char) => char.charCodeAt(0) < 32)) return "/";
  if (value === "/auth" || value.startsWith("/auth?")) return "/";
  return value;
}
