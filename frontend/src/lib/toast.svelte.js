export const toasts = $state({ items: [] });
let n = 0;
export function toast(message, kind = 'ok', ms = 3500) {
  const id = ++n;
  toasts.items.push({ id, message, kind });
  setTimeout(() => { toasts.items = toasts.items.filter((t) => t.id !== id); }, ms);
}
export async function attempt(fn, okMsg) {
  try { const r = await fn(); if (okMsg) toast(okMsg); return r; }
  catch (e) { toast(e.message || String(e), 'bad', 7000); throw e; }
}
