import Link from "next/link";

/**
 * S1 - the landing screen.
 *
 * The prototype's shape: a claim on the left over three numbered rules, and the
 * way in on the right. The three are not marketing. They are the three things
 * this build actually refuses to do differently, each of which is enforced
 * somewhere a screen cannot reach - the versioning is in the incident graph, the
 * approval floor is in the domain and cannot be configured away, and the closure
 * gate reads the application rather than the platform's own opinion of it.
 *
 * Whoever reads this page next: if one of these three stops being true, delete
 * the line. A landing page that describes a product that used to exist is worse
 * than one that describes nothing.
 */

const RULES = [
  {
    title: "Evidence carries its own history",
    body: "Every fact, assumption and hypothesis is versioned, and each confidence number can be traced to the evidence that moved it and when. Nothing is averaged into a score whose parts cannot be recovered.",
  },
  {
    title: "A person owns every lever",
    body: "Nothing that changes the world executes without a policy, its prerequisites and a named approver. High-risk actions can never auto-execute, and that is a rule the domain enforces rather than a setting somebody can turn off.",
  },
  {
    title: "Recovery is judged in four layers",
    body: "Technical, service, customer and business must all pass, and then hold through a stability window. A healthy estate with a failing customer journey has not recovered, and closure is refused.",
  },
];

export function Landing({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid items-start gap-10 lg:grid-cols-[1fr_minmax(320px,440px)]">
      <div className="flex min-w-0 flex-col gap-6">
        <div>
          <h6 className="m-0 text-ink-muted">Major incident intelligence</h6>
          <h1 className="mt-2 max-w-[14ch] text-h1">
            The incident command centre that reasons in the open.
          </h1>
        </div>

        <ol className="flex flex-col border-t-2 border-line">
          {RULES.map((rule, index) => (
            <li
              key={rule.title}
              className="grid grid-cols-[40px_1fr] items-start gap-4 border-b border-line py-5 sm:grid-cols-[40px_220px_1fr]"
            >
              <span className="font-mono text-dense text-accent">
                {String(index + 1).padStart(2, "0")}
              </span>
              <h2 className="text-h5">{rule.title}</h2>
              <p className="m-0 text-dense text-ink-muted">{rule.body}</p>
            </li>
          ))}
        </ol>

        <p className="text-small text-ink-muted">
          Already have an organisation?{" "}
          <Link href="/login" className="text-accent hover:underline">
            Sign in
          </Link>
          .
        </p>
      </div>

      <div className="min-w-0">{children}</div>
    </div>
  );
}
