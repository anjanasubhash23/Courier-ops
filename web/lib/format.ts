// Show times in India Standard Time, where the network operates.
const dateTime = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata",
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

export const when = (iso: string | null | undefined) => (iso ? dateTime.format(new Date(iso)) : "–");

export const label = (value: string) => value.replaceAll("_", " ");

export const count = (n: number) => n.toLocaleString("en-IN");
