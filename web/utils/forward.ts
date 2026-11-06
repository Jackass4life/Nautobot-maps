/**
 * Identity headers server-side rendering may pass on to the API (#582).
 *
 * The API believes them from web's address, so web must not pass on what a
 * client sent straight to its port: only when the request carries the sign-in
 * proxy's secret (the same AUTH_PROXY_SECRET the API checks). No secret
 * configured: nothing is forwarded and pages render as not signed in.
 */
export function identityHeaders(
  incoming: Record<string, string | undefined>,
  names: string[],
  secret: string,
  secretHeader = 'x-auth-proxy-secret',
): Record<string, string> {
  if (!secret || !sameSecret(incoming[secretHeader] ?? '', secret)) return {}
  const out: Record<string, string> = { [secretHeader]: secret }
  for (const name of names) {
    const value = incoming[name]
    if (value !== undefined) out[name] = value
  }
  return out
}

/** Constant-time for equal lengths, like the API's hmac.compare_digest. */
function sameSecret(sent: string, secret: string): boolean {
  let diff = sent.length ^ secret.length
  for (let i = 0; i < secret.length; i++) diff |= (sent.charCodeAt(i) || 0) ^ secret.charCodeAt(i)
  return diff === 0
}
