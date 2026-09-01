import Link from "next/link";

import { PolicyEditor } from "@/features/governance/components/PolicyEditor";

/**
 * S21 - the execution policy for one application.
 *
 * Its own route rather than a tab on the app detail screen: editing a policy is
 * a different act from watching an application, it carries a different
 * permission (`edit_policies`), and it is a thing somebody links a colleague to.
 */
export default function PolicyPage({ params }: { params: { appId: string } }) {
  return (
    <div className="space-y-3">
      <Link
        href={`/apps/${encodeURIComponent(params.appId)}`}
        className="text-xs text-ink-muted underline decoration-line hover:text-accent-strong"
      >
        ← {params.appId}
      </Link>
      <PolicyEditor appId={params.appId} />
    </div>
  );
}
