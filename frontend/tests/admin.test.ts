// Administration in the web pages (#530).
import { describe, expect, it } from "vitest";
import {
  channelLine,
  day,
  expiryBody,
  inactiveText,
  purgedText,
  randomSuffix,
  shareUrl,
  statusTokenName,
  tokenMatches,
  validName,
  wirelessLine,
  type Token,
} from "~/utils/admin";

const now = new Date("2026-10-28T21:05:00Z");

function token(extra: Partial<Token> = {}): Token {
  return {
    id: 1,
    name: "librenms",
    prefix: "nmt_abcd1234",
    role: "operator",
    tenant: null,
    created_by: "olga",
    created_at: "2026-10-01T10:00:00Z",
    expires_at: null,
    last_used_at: null,
    revoked_at: null,
    revoked_by: "",
    state: "active",
    ...extra,
  };
}

describe("tokens", () => {
  it("a customer's token's name is one the API takes", () => {
    expect(statusTokenName("Acme Corp", now)).toBe("Acme Corp status 2026-10-28");
    expect(statusTokenName("Nord A/S", now)).toBe("Nord A-S status 2026-10-28");
    expect(statusTokenName("Ørsted (DK)", now, "k3x9a")).toBe("rsted -DK- status 2026-10-28 k3x9a");
    expect(statusTokenName("***", now)).toBe("tenant status 2026-10-28");
    const long = statusTokenName("x".repeat(300), now);
    expect(long.length).toBe(100);
    expect(statusTokenName("x".repeat(300), now, randomSuffix()).length).toBe(100);
    expect(randomSuffix()).toMatch(/^[a-z0-9]{5}$/);
    for (const name of [statusTokenName("Nord A/S", now), statusTokenName("Ørsted (DK)", now, randomSuffix()), long]) {
      expect(validName(name)).toBe(true);
    }
    expect(validName(" ")).toBe(false);
    expect(validName("-x")).toBe(false);
    expect(validName("a/b")).toBe(false);
  });

  it("found by name, role, tenant, first characters or who made it", () => {
    const status = token({ name: "acme web", role: "status", tenant: "Acme Corp" });
    expect(tokenMatches(status, "")).toBe(true);
    expect(tokenMatches(status, "acme corp")).toBe(true);
    expect(tokenMatches(status, "STATUS olga")).toBe(true);
    expect(tokenMatches(status, "nmt_abcd")).toBe(true);
    expect(tokenMatches(status, "nordnet")).toBe(false);
  });

  it("how long a new token works", () => {
    expect(expiryBody("365")).toEqual({ expires_in_days: 365 });
    expect(expiryBody("never")).toEqual({});
  });

  it("what the page says", () => {
    expect(day(null)).toBe("never");
    expect(day("2027-10-28T21:05:00Z")).toBe("2027-10-28");
    expect(inactiveText(0)).toBe("");
    expect(inactiveText(1234)).toBe("+1,234 revoked or expired");
    expect(purgedText(3)).toBe("Deleted 3 tokens; their names are free again");
    expect(purgedText(1, true)).toBe("1 token would be deleted");
    expect(purgedText(0)).toBe("Nothing to delete");
  });
});

describe("sharing a status page", () => {
  it("the key after #, never sent to the server", () => {
    expect(shareUrl("https://maps.example.com", "/status/abc", "nmt_x")).toBe(
      "https://maps.example.com/status/abc#key=nmt_x",
    );
    expect(shareUrl("https://maps.example.com", "/status/abc", "")).toBe("https://maps.example.com/status/abc");
  });
});

describe("checks", () => {
  const base = { controller: "Catalyst Center dk", kind: "catalyst_center" as const, url: "https://dnac" };
  it("a wireless controller in a line", () => {
    expect(
      wirelessLine({ ...base, reachable: true, signed_in: true, access_points: 4210, down: 12, matched: 4198 }),
    ).toBe("4,210 access points, 12 down, 4,198 match a Nautobot device");
    expect(wirelessLine({ ...base, reachable: false, signed_in: false, error: "can't connect" })).toBe(
      "Can't reach it: can't connect",
    );
    expect(wirelessLine({ ...base, reachable: true, signed_in: false, error: "the account was refused" })).toBe(
      "Signed in: no: the account was refused",
    );
    expect(wirelessLine({ ...base, reachable: true, signed_in: null, error: "answered HTTP 500" })).toBe(
      "Can't read it: answered HTTP 500",
    );
  });

  it("a test notification per channel", () => {
    expect(channelLine("teams", "ok")).toEqual({ channel: "Microsoft Teams", ok: true, text: "Sent" });
    expect(channelLine("email", "error: refused")).toEqual({ channel: "Email", ok: false, text: "error: refused" });
  });
});
