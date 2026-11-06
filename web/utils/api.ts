// An answer of the typed client (`$api`, #609) as the pages handle it: the data, or a
// thrown error with the API's body (`err.data.error`) and status (`err.statusCode`),
// as `$fetch` threw it before (`apiError` / `errorMessage` in utils/format.ts read it).
export class ApiRequestError extends Error {
  constructor(
    readonly statusCode: number,
    readonly data: unknown,
  ) {
    const message = (data as { error?: unknown } | null)?.error;
    super(typeof message === "string" && message ? message : `Request failed (${statusCode})`);
  }
}

export async function unwrap<T>(
  request: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await request;
  if (error !== undefined || !response.ok) throw new ApiRequestError(response.status, error ?? null);
  return data as T;
}

// A query string ("tenant=a&q=b", a page's own filters) as the client's query object;
// a repeated key keeps every value.
export function queryOf(search: string | URLSearchParams): Record<string, string | string[]> {
  const params = typeof search === "string" ? new URLSearchParams(search.replace(/^\?/, "")) : search;
  const query: Record<string, string | string[]> = {};
  for (const [key, value] of params) {
    const seen = query[key];
    query[key] = seen === undefined ? value : Array.isArray(seen) ? [...seen, value] : [seen, value];
  }
  return query;
}
