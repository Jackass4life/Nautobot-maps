import { describe, expect, it } from "vitest";
import { choiceItems, mergedDescriptions } from "../app/utils/tenants";

describe("tenant choices (#497)", () => {
  it("a description under the name, when there is one", () => {
    expect(choiceItems(["Acme", "NNIT"], { NNIT: "Novo Nordisk IT", Acme: "" })).toEqual([
      { label: "Acme", value: "Acme" },
      { label: "NNIT", value: "NNIT", description: "Novo Nordisk IT" },
    ]);
    expect(choiceItems(["toString", "__proto__"], {})).toEqual([
      { label: "toString", value: "toString" },
      { label: "__proto__", value: "__proto__" },
    ]);
  });

  it("every row's descriptions, a tenant named __proto__ too", () => {
    const rows = Array.from({ length: 1200 }, (_, i) => ({ tenant_descriptions: { [`T${i % 40}`]: `Tenant ${i % 40}` } }));
    const merged = mergedDescriptions([...rows, {}, { tenant_descriptions: JSON.parse('{"__proto__": "Odd"}') }]);
    expect(Object.keys(merged)).toHaveLength(41);
    expect(merged.T7).toBe("Tenant 7");
    expect(Object.hasOwn(merged, "__proto__") && merged["__proto__"]).toBe("Odd");
  });
});
