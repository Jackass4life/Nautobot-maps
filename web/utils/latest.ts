// Only the newest of overlapping loads counts (#605): a slow answer to an
// older request (an earlier period, site or guide) must not overwrite the
// newer one's.  Each load takes a ticket; after its await it applies its
// result, or its error, only while the ticket is still the newest.
export function latestOnly(): () => () => boolean {
  let newest = 0;
  return () => {
    const ticket = ++newest;
    return () => ticket === newest;
  };
}
