// Relative cutoffs are elapsed time, independent of local daylight-saving changes.
export function relativeCutoff(unit, input, now = Date.now()) {
  const limits = { days: [36500, 86400000], hours: [876000, 3600000] };
  if (!Object.hasOwn(limits, unit)) throw new Error("Choose days or hours");
  const [maximum, milliseconds] = limits[unit];
  const amount = Number(input);
  if (!Number.isInteger(amount) || amount < 1 || amount > maximum) {
    throw new Error(`Enter a whole number of ${unit} between 1 and ${maximum}`);
  }
  return new Date(now - amount * milliseconds);
}
