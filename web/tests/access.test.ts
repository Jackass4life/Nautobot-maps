// The Admin page's identities and roles (#721): a form checks what the API checks
// (api/tests/test_role_changes.py runs the API on the same cases).
import { describe, expect, it } from "vitest";
import cases from "./fixtures/access-cases.json";
import {
  deleteIdentityText,
  deleteRoleText,
  identityMatches,
  normalScope,
  splitScopes,
  validIdentityName,
  validLanding,
  validRoleName,
  type Identity,
  type RoleInfo,
} from "~/utils/access";

describe("a scope as the API stores it", () => {
  for (const [typed, stored] of Object.entries(cases.scopes)) {
    it(JSON.stringify(typed), () => expect(normalScope(typed)).toBe(stored));
  }
  it("up to 255 characters", () => {
    expect(normalScope("a".repeat(255))).toBe("a".repeat(255));
    expect(normalScope("a".repeat(256))).toBeNull();
  });
});

describe("a role's name as the API takes it", () => {
  for (const [name, valid] of Object.entries(cases.role_names)) {
    it(JSON.stringify(name), () => expect(validRoleName(name)).toBe(valid));
  }
});

describe("the other fields", () => {
  it("an identity's name: no spaces", () => {
    expect(validIdentityName(" jdoe ")).toBe(true);
    expect(validIdentityName("j doe")).toBe(false);
    expect(validIdentityName("")).toBe(false);
  });
  it("a landing page: this app's", () => {
    expect(validLanding("/noc")).toBe(true);
    expect(validLanding("//evil.example")).toBe(false);
    expect(validLanding("https://evil.example")).toBe(false);
    expect(validLanding("/a b")).toBe(false);
  });
  it("several scopes in one field", () => {
    expect(splitScopes("alerts:ack, Page:Wall alerts:ack  bad::x")).toEqual({
      scopes: ["alerts:ack", "page:wall"],
      bad: ["bad::x"],
    });
  });
});

const jane: Identity = {
  unique_name: "jdoe",
  common_name: "Jane Doe",
  description: "",
  roles: ["noc"],
  scopes: ["maintenance:write"],
  keys: 2,
  for_a_key: false,
};

describe("the list", () => {
  it("finds by name, display name, role or scope; every word", () => {
    expect(identityMatches(jane, "jane")).toBe(true);
    expect(identityMatches(jane, "NOC maintenance")).toBe(true);
    expect(identityMatches(jane, "noc admin")).toBe(false);
    expect(identityMatches(jane, "")).toBe(true);
  });
  it("says what a delete does", () => {
    expect(deleteIdentityText(jane)).toBe("jdoe and 2 keys stop working.");
    expect(deleteIdentityText({ ...jane, keys: 0 })).toBe(
      "jdoe can no longer sign in as an identity.",
    );
    const role = { identities: 1 } as RoleInfo;
    expect(deleteRoleText(role)).toBe("1 identity loses it.");
    expect(deleteRoleText({ ...role, identities: 0 })).toBe("Nobody has it.");
  });
});
