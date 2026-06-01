export function nextTextDelta(previous: string, current: string): string {
  if (!current || previous === current) {
    return "";
  }
  if (current.startsWith(previous)) {
    return current.slice(previous.length);
  }
  if (previous.endsWith(current)) {
    return "";
  }
  const maxOverlap = Math.min(previous.length, current.length);
  for (let overlap = maxOverlap; overlap > 0; overlap -= 1) {
    if (previous.endsWith(current.slice(0, overlap))) {
      return current.slice(overlap);
    }
  }
  return current;
}
