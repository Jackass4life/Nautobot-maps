// Administration in the web pages (#530): API tokens, a tenant's status page
// link to share, the test notification and the wireless controllers' check.
// What the API can do, the pages can do too.
import type { components } from "~/api/schema";
import { hasAllWords, searchWords } from "./format";

export type Token = components["schemas"]["Token"];
export type NewToken = components["schemas"]["NewToken"];
export type WirelessCheck = components["schemas"]["WirelessCheck"];
export type WirelessControllerCheck = components["schemas"]["WirelessControllerCheck"];
export type Tenant = components["schemas"]["Tenant"];

export const ROLES = ["viewer", "operator", "admin", "status"] as const;
export type Role = (typeof ROLES)[number];
// What each role may do, said where the role is picked.
export const ROLE_HINTS: Record<Role, string> = {
  viewer: "Reads the map, the board and the read APIs",
  operator: "Also cases, history, maintenance (LibreNMS, Alertmanager, scripts)",
  admin: "Everything, tokens included",
  status: "Opens one tenant's status page, nothing else (for a customer)",
};
// How long a new token works, as a menu's values ("never": until revoked).
export const EXPIRY_CHOICES = [
  { label: "30 days", value: "30" },
  { label: "90 days", value: "90" },
  { label: "1 year", value: "365" },
  { label: "Never", value: "never" },
];

// A choice's expires_in_days for the API: none for "never".
export function expiryBody(choice: string): { expires_in_days?: number } {
  const days = Number.parseInt(choice, 10);
  return Number.isFinite(days) && days > 0 ? { expires_in_days: days } : {};
}
// Past this many tokens the table gets a search box (hundreds over time).
export const TOKEN_SEARCH_FROM = 25;
// What the API takes as a token's name (tokens.NAME_PATTERN, 100 characters).
const NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9 ._-]*$/;
export const MAX_NAME_LENGTH = 100;

export function validName(name: string): boolean {
  const trimmed = name.trim();
  return trimmed.length > 0 && trimmed.length <= MAX_NAME_LENGTH && NAME_PATTERN.test(trimmed);
}

// A customer's token's name, from its tenant and today: "Acme Corp status
// 2026-10-28".  Characters a name can't have become "-"; a name must be new,
// so *extra* (a few random letters) is added when the day's is taken.
export function statusTokenName(tenant: string, now: Date = new Date(), extra = ""): string {
  const day = now.toISOString().slice(0, 10);
  const suffix = ` status ${day}${extra ? ` ${extra}` : ""}`;
  const base = tenant
    .replace(/[^A-Za-z0-9 ._-]+/g, "-")
    .replace(/^[^A-Za-z0-9]+/, "")
    .trim()
    .slice(0, MAX_NAME_LENGTH - suffix.length)
    .trim();
  return `${base || "tenant"}${suffix}`;
}

// A few random letters and digits, for a name that must be new.
export function randomSuffix(): string {
  return Math.random().toString(36).slice(2, 7).padEnd(5, "0");
}

// The link to send a customer: the page and the key after "#", which the
// browser never sends to the server (#291).
export function shareUrl(origin: string, statusLink: string, key: string): string {
  return `${origin}${statusLink}${key ? `#key=${key}` : ""}`;
}

// "never", "2027-10-28": a token's date, the day is enough here.
export function day(value: string | null | undefined, never = "never"): string {
  return value ? value.slice(0, 10) : never;
}

// A token is found by its name, role, tenant, first characters or who made it.
export function tokenMatches(token: Token, query: string): boolean {
  const words = searchWords(query);
  if (!words.length) return true;
  return hasAllWords(
    [token.name, token.role, token.tenant || "", token.prefix, token.created_by].join(" ").toLowerCase(),
    words,
  );
}

// "+3 revoked or expired", or "" when there are none.
export function inactiveText(count: number): string {
  return count ? `+${count.toLocaleString("en")} revoked or expired` : "";
}

// A purge's answer in words.
export function purgedText(count: number, dryRun = false): string {
  const tokens = `${count.toLocaleString("en")} token${count === 1 ? "" : "s"}`;
  if (dryRun) return count ? `${tokens} would be deleted` : "Nothing to delete";
  return count ? `Deleted ${tokens}; their names are free again` : "Nothing to delete";
}

// One wireless controller's check in a line: "4,210 access points, 12 down,
// 4,198 match a Nautobot device", or why it can't be read.
export function wirelessLine(result: WirelessControllerCheck): string {
  if (result.error) {
    const where = !result.reachable ? "Can't reach it" : result.signed_in === false ? "Signed in: no" : "Can't read it";
    return `${where}: ${result.error}`;
  }
  const n = (value: number | null | undefined) => (value ?? 0).toLocaleString("en");
  return `${n(result.access_points)} access points, ${n(result.down)} down, ${n(result.matched)} match a Nautobot device`;
}

// A channel's test result: "ok", or the error the API says.
export function channelLine(channel: string, result: string): { channel: string; ok: boolean; text: string } {
  const names: Record<string, string> = { webhook: "Webhook", teams: "Microsoft Teams", email: "Email" };
  return { channel: names[channel] || channel, ok: result === "ok", text: result === "ok" ? "Sent" : result };
}
