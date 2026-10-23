// The board's sites with missing or wrong coordinates (#368): a line under
// the map's count, and the list behind its Check button.
import type { components } from "~/api/schema";

export type CoordinateProblem = components["schemas"]["CoordinateProblem"];

// The list shows this many; the CSV has every one.
export const COORDINATE_LIST_LIMIT = 50;
// A screen left open picks up fixes made in Nautobot.
export const COORDINATE_REFRESH_MS = 15 * 60 * 1000;

const WRONG = new Set(["invalid", "zero", "swapped"]);
const sites = (n: number, one: string, many: string) => `${n} site${n === 1 ? "" : "s"} ${n === 1 ? one : many}`;

// "12 sites aren't on the map · 2 have wrong coordinates", or "" when all is
// well.  "Far" is a hint, not an error (#416): said only when nothing is wrong.
export function coordinateSummary(problems: Pick<CoordinateProblem, "problem">[]): string {
  const missing = problems.filter((item) => item.problem === "missing").length;
  const wrong = problems.filter((item) => WRONG.has(item.problem)).length;
  const far = problems.length - missing - wrong;
  const parts: string[] = [];
  if (missing) parts.push(sites(missing, "isn't on the map", "aren't on the map"));
  if (wrong) {
    parts.push(
      missing
        ? `${wrong} ${wrong === 1 ? "has" : "have"} wrong coordinates`
        : sites(wrong, "has wrong coordinates", "have wrong coordinates"),
    );
  }
  if (!parts.length && far) parts.push(sites(far, "is far from the others", "are far from the others"));
  return parts.join(" · ");
}

// The list: worst first (the server's order), the first COORDINATE_LIST_LIMIT,
// the "far" hints last under their own quiet heading (#416), and how many more.
export function coordinateList(problems: CoordinateProblem[]): {
  wrong: CoordinateProblem[];
  far: CoordinateProblem[];
  more: number;
} {
  const shown = problems.slice(0, COORDINATE_LIST_LIMIT);
  return {
    wrong: shown.filter((item) => item.problem !== "far"),
    far: shown.filter((item) => item.problem === "far"),
    more: problems.length - shown.length,
  };
}

// Show only for a site on the map: none with no or impossible coordinates.
export function canShow(item: Pick<CoordinateProblem, "latitude" | "problem">): boolean {
  return item.latitude != null && item.problem !== "invalid";
}
