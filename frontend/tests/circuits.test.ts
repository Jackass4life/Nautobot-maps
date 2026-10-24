// Circuit mapping (#385; on Nuxt since #463): grouped, searched, summarised.
import { describe, expect, it } from "vitest";
import { circuitKind, GROUPS, matches, prepare, CIRCUITS_STEP, summaryText, type MappedCircuit } from "~/utils/circuits";
import { nautobotCircuitUrl, nautobotLocationUrl, searchWords } from "~/utils/format";

const circuit = (fields: Partial<MappedCircuit>): MappedCircuit => ({
  id: "c",
  cid: "C",
  provider: "",
  circuit_type: "",
  status: "",
  tenant: "",
  commit_rate: "",
  description: "",
  group: "none",
  sites: [],
  ...fields,
});

describe("the circuit mapping", () => {
  const circuits = prepare([
    circuit({
      id: "c1",
      cid: "TDC-1",
      group: "one",
      provider: "Telia",
      status: "Active",
      description: "Roersvej 33, 5000 Odense",
      sites: [{ id: "loc 1", name: "Odense", on_map: true }],
    }),
    circuit({ id: "c2", cid: "GTT-2", group: "several" }),
  ]);

  it("worst first: several sites, no site, one site", () => {
    expect(GROUPS.map((group) => group.id)).toEqual(["several", "none", "one"]);
    expect(CIRCUITS_STEP).toBe(100);
  });

  it("searches every word in circuit, provider, address and site", () => {
    expect(matches(circuits[0]!, searchWords("odense telia"))).toBe(true);
    expect(matches(circuits[0]!, searchWords("odense gtt"))).toBe(false);
    expect(matches(circuits[1]!, searchWords("gtt"))).toBe(true);
  });

  it("what is known about it, nothing empty", () => {
    expect(circuitKind(circuit({ provider: "Telia", commit_rate: "10 Gbps", tenant: "Acme" }))).toBe(
      "Telia · 10 Gbps · Acme",
    );
    expect(circuitKind(circuit({}))).toBe("");
  });

  it("the summary line", () => {
    const summary = { total: 152, several: 1, none: 150, one: 1 };
    expect(summaryText(summary)).toBe("152 circuits without a termination: 1 several sites · 150 no site · 1 one site");
    expect(summaryText(summary, 3).endsWith(" · 3 match the search")).toBe(true);
    expect(summaryText(summary, 152)).not.toContain("match");
    expect(summaryText({ total: 0 })).toBe("Every circuit has a termination at a location.");
  });

  it("links to Nautobot", () => {
    expect(nautobotCircuitUrl("https://nb.example/", "c 1")).toBe("https://nb.example/circuits/circuits/c%201/");
    expect(nautobotLocationUrl("https://nb.example", "b")).toBe("https://nb.example/dcim/locations/b/");
  });
});
