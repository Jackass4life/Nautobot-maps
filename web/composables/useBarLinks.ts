// The ?tenant= / ?q= the top bar's links keep (#367), set by the map and the board:
// the bar is the layout's (#737), not theirs, so they say it here.
export function useBarLinks() {
  const linkQuery = useState("bar-link-query", () => "");
  /** Keep *query* on the bar's links while the calling page is shown. */
  function keep(query: Ref<string> | ComputedRef<string>) {
    watchEffect(() => (linkQuery.value = query.value));
    onBeforeUnmount(() => (linkQuery.value = ""));
  }
  return { linkQuery, keep };
}
