// Circuit mapping (#385; static/js/circuits/list.js): circuits without a
// termination, grouped by what their address found, worst first, each group
// folded with a count and the first STEP, "Show N more".
import type { components } from "~/api/schema";
import { hasAllWords } from "./format";

export type MappedCircuit = components["schemas"]["MappedCircuit"];
export type CircuitMapping = components["schemas"]["CircuitMapping"];
export type CircuitGroup = MappedCircuit["group"];
export type SearchableCircuit = MappedCircuit & { text: string };

// Circuits shown per group, and per "Show more".
export const STEP = 100;

// Worst first: a choice to make, then nothing found, then ready to terminate.
export const GROUPS: { id: CircuitGroup; title: string; hint: string }[] = [
  { id: "several", title: "Several sites", hint: "The address belongs to more than one site: choose where to terminate it." },
  { id: "none", title: "No site", hint: "The description isn't an address of any site: a different address, more text, or no address." },
  { id: "one", title: "One site", hint: "Ready: add the termination at this site." },
];

// Lower-cased text a search looks in, worked out once per circuit.
export function prepare(circuits: MappedCircuit[]): SearchableCircuit[] {
  return circuits.map((circuit) => ({
    ...circuit,
    text: [
      circuit.cid,
      circuit.provider,
      circuit.circuit_type,
      circuit.status,
      circuit.tenant,
      circuit.description,
      ...(circuit.sites || []).map((site) => site.name),
    ]
      .join(" ")
      .toLowerCase(),
  }));
}

export function matches(circuit: SearchableCircuit, words: string[]): boolean {
  return hasAllWords(circuit.text, words);
}

// "Telia · Internet · 10 Gbps · Acme": what is known, nothing empty.
export function circuitKind(circuit: MappedCircuit): string {
  return [circuit.provider, circuit.circuit_type, circuit.commit_rate, circuit.tenant].filter(Boolean).join(" · ");
}

// "312 circuits without a termination: 12 several sites · 60 no site · 240 one site".
export function summaryText(summary: Partial<CircuitMapping["summary"]>, shown: number | null = null): string {
  const total = summary.total || 0;
  if (!total) return "Every circuit has a termination at a location.";
  const parts = `${summary.several || 0} several sites · ${summary.none || 0} no site · ${summary.one || 0} one site`;
  const found = shown === null || shown === total ? "" : ` · ${shown} match the search`;
  return `${total} circuit${total === 1 ? "" : "s"} without a termination: ${parts}${found}`;
}
