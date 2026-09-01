import { MobilePaging } from "@/features/warroom/components/MobilePaging";

/**
 * S22 - the mobile paging page. A route of its own rather than a panel inside
 * the cockpit, because the person opening it is on a phone and has one decision
 * to make.
 */
export default function MyPagesPage() {
  return <MobilePaging />;
}
