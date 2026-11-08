// Overlapping loads, answered out of order (#605).
import { describe, expect, it } from "vitest";
import { latestOnly } from "~/utils/latest";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

// A page's load, as maintenance.vue and help/[[slug]].vue do it.
function page() {
  const nextLoad = latestOnly();
  const shown = { value: "", error: "" };
  async function load(request: Promise<string>) {
    const isNewest = nextLoad();
    try {
      const value = await request;
      if (!isNewest()) return;
      shown.value = value;
      shown.error = "";
    } catch {
      if (!isNewest()) return;
      shown.error = "could not load";
    }
  }
  return { shown, load };
}

describe("latestOnly", () => {
  it("keeps the newest answer when an older one comes last", async () => {
    const { shown, load } = page();
    const first = deferred<string>();
    const second = deferred<string>();
    const loads = [load(first.promise), load(second.promise)];
    second.resolve("October");
    first.resolve("September");
    await Promise.all(loads);
    expect(shown.value).toBe("October");
  });

  it("applies answers in order as usual", async () => {
    const { shown, load } = page();
    const first = deferred<string>();
    const second = deferred<string>();
    const loads = [load(first.promise), load(second.promise)];
    first.resolve("September");
    await loads[0];
    expect(shown.value).toBe("");
    second.resolve("October");
    await loads[1];
    expect(shown.value).toBe("October");
  });

  it("ignores an older request's error", async () => {
    const { shown, load } = page();
    const first = deferred<string>();
    const second = deferred<string>();
    const loads = [load(first.promise), load(second.promise)];
    second.resolve("October");
    first.reject(new Error("timeout"));
    await Promise.all(loads);
    expect(shown).toEqual({ value: "October", error: "" });
  });

  it("shows the newest request's error over an older answer", async () => {
    const { shown, load } = page();
    const first = deferred<string>();
    const second = deferred<string>();
    const loads = [load(first.promise), load(second.promise)];
    second.reject(new Error("timeout"));
    first.resolve("September");
    await Promise.all(loads);
    expect(shown).toEqual({ value: "", error: "could not load" });
  });

  it("drops a pending load when a new ticket is taken, as leaving the page does", () => {
    const nextLoad = latestOnly();
    const isNewest = nextLoad();
    nextLoad(); // onBeforeUnmount
    expect(isNewest()).toBe(false);
  });

  it("keeps each page's loads apart", () => {
    const a = latestOnly()();
    const b = latestOnly()();
    expect(a()).toBe(true);
    expect(b()).toBe(true);
  });
});
