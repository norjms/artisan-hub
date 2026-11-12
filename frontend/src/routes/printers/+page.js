// This page moved (the sidebar was regrouped). The server redirects too; this covers links inside the app.
import { redirect } from '@sveltejs/kit';

export function load({ url }) {
  const q = url.searchParams.toString();
  redirect(301, '/slicing?tab=printers' + (q ? '&' + q : ''));
}
