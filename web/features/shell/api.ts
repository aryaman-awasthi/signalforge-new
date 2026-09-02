/** What the application shell knows about who is looking at it.
 *
 * Two reads, both through the typed client. The shell shows the organisation
 * because a command centre that does not say whose estate you are looking at is
 * one wrong tab away from an expensive mistake, and it shows the role because
 * every refusal in this product is a role refusing, and a reader who cannot see
 * their own role cannot understand the refusal.
 */

import { proxyApi } from "@/lib/api/client";

export type Org = { id: string; name: string; slug: string; created_at: string };

export function fetchOrg() {
  return proxyApi.get<Org>("/orgs/current");
}
