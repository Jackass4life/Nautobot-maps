// A tenant filter's choices (#497): found by the tenant's name or its
// description ("Novo" finds NNIT, "Novo Nordisk IT"), the description shown
// under the name so it is clear why.  Nuxt UI's menus search these fields.
export interface ChoiceItem {
  label: string;
  value: string;
  description?: string;
}

export const CHOICE_SEARCH_FIELDS = ["label", "description"];

export function choiceItems(values: string[], descriptions: Record<string, string> = {}): ChoiceItem[] {
  return values.map((value) => {
    const description = Object.hasOwn(descriptions, value) ? descriptions[value] : "";
    return description ? { label: value, value, description } : { label: value, value };
  });
}

// Every tenant's description, from rows that each carry their own tenants'.
export function mergedDescriptions(rows: { tenant_descriptions?: Record<string, string> }[]): Record<string, string> {
  // No prototype: a tenant named "__proto__" is a name like any other.
  const merged: Record<string, string> = Object.create(null);
  for (const row of rows) Object.assign(merged, row.tenant_descriptions || {});
  return merged;
}
