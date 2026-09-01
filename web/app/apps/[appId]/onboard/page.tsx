import { redirect } from "next/navigation";

/** The wizard's steps are nested routes 1..7; /onboard alone means step one. */
export default function OnboardIndexPage({
  params,
}: {
  params: { appId: string };
}) {
  redirect(`/apps/${encodeURIComponent(params.appId)}/onboard/1`);
}
