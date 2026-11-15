// The Admin page's identities and roles (#721): the same checks the API makes
// (identity_service.check_scope, role_service.ROLE_NAME), so a form says what is
// wrong before it is sent.  web/tests/fixtures/access-cases.json holds the
// cases; api/tests/test_role_changes.py checks the API against the same file.
import type { components } from "~/api/api.schema";

export type Identity = components["schemas"]["IdentityDto"];
export type RoleInfo = components["schemas"]["RoleDto"];

const MAX_LENGTH = 255;
const MAX_ROLE_NAME = 64;
const SCOPE_PART = /^[a-z0-9_*-]+$/;
const ROLE_NAME = /^[a-z][a-z0-9_-]*$/;

/** A scope as the API stores it (trimmed, lowercase), or null when it can't be one:
 *  parts of a-z, 0-9, _ - or *, separated by ":". */
export function normalScope(scope: string): string | null {
  const value = scope.trim().toLowerCase();
  if (!value || value.length > MAX_LENGTH) return null;
  return value.split(":").every((part) => SCOPE_PART.test(part)) ? value : null;
}

export const SCOPE_HINT =
  "Parts of a-z, 0-9, _ - or *, separated by ':' (e.g. alerts:ack, alerts:*)";

export function validRoleName(name: string): boolean {
  const value = name.trim().toLowerCase();
  return value.length <= MAX_ROLE_NAME && ROLE_NAME.test(value) && value !== "status";
}

/** The user name a sign-in gives: no spaces, up to 255 characters. */
export function validIdentityName(name: string): boolean {
  const value = name.trim();
  return value.length > 0 && value.length <= MAX_LENGTH && !/\s/.test(value);
}

/** A page of this app to open on: a path, never another site's. */
export function validLanding(landing: string): boolean {
  const value = landing.trim();
  return value.startsWith("/") && !value.startsWith("//") && !/\s/.test(value);
}

/** Scopes typed in one field: by commas or spaces, each checked; the bad ones. */
export function splitScopes(text: string): { scopes: string[]; bad: string[] } {
  const scopes: string[] = [];
  const bad: string[] = [];
  for (const part of text.split(/[\s,]+/)) {
    if (!part) continue;
    const scope = normalScope(part);
    if (scope === null) bad.push(part);
    else if (!scopes.includes(scope)) scopes.push(scope);
  }
  return { scopes, bad };
}

export function identityMatches(identity: Identity, query: string): boolean {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const text = [
    identity.unique_name,
    identity.common_name,
    identity.description,
    ...identity.roles,
    ...identity.scopes,
  ]
    .join(" ")
    .toLowerCase();
  return words.every((word) => text.includes(word));
}

export function keysText(count: number): string {
  return count === 1 ? "1 key" : `${count.toLocaleString("en")} keys`;
}

/** What deleting an identity does, said on its Sure? button's row. */
export function deleteIdentityText(identity: Identity): string {
  return identity.keys
    ? `${identity.unique_name} and ${keysText(identity.keys)} stop working.`
    : `${identity.unique_name} can no longer sign in as an identity.`;
}

export function deleteRoleText(role: RoleInfo): string {
  const who = role.identities === 1 ? "1 identity loses" : `${role.identities} identities lose`;
  return role.identities ? `${who} it.` : "Nobody has it.";
}
