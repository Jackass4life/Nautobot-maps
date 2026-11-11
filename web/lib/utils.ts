import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

// shadcn-vue's class helper (#640): joins classes, the last of two Tailwind
// classes that clash wins.  The app's own helpers are in utils/.
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
