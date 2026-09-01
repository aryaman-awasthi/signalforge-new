# PaySphere configuration repository

The application team's config repo, and the deployment pipeline that applies it.
It is part of the reference harness (demo data, not product): deleting it leaves
SignalForge working and only removes the evidence that a real pipeline can
announce a deployment.

```
shared.yml     values common to both regions, and the release identifier
region-a.yml   region-a settings
region-b.yml   region-b settings
deploy.sh      applies a release to one region and tells SignalForge
```

## Using it

```bash
export SIGNALFORGE_INGEST_URL=http://localhost:8000/api/v1/ingest/changes
export SIGNALFORGE_INGEST_TOKEN=sfi_...      # issued by SignalForge for the "changes" stream
export SIGNALFORGE_INGEST_SECRET=...         # the HMAC secret issued alongside it
export SIGNALFORGE_INGEST_KEY_ID=...         # optional; identifies the secret after a rotation

./deploy.sh --region region-b                # apply and announce
./deploy.sh --region region-b --release v13  # override the release in shared.yml
./deploy.sh --region region-b --dry-run      # print the event ids and change nothing
```

It needs `bash`, `curl` and `openssl` and nothing else. There is no SignalForge
library here, and there is nothing to install: the announcement is a signed POST
of a canonical `ChangeEvent` to a frozen public path.

```
POST /api/v1/ingest/changes
Authorization: Bearer <token>
X-SignalForge-Timestamp: <unix seconds>
X-SignalForge-Signature: <hex hmac-sha256 over "<timestamp>.<raw body>">
```

## Two decisions worth stating

**The event id is derived, not random.** It is a UUIDv5-shaped digest of
`(release, region, status)`. Re-triggering a pipeline - a retried job, a
re-run stage, an operator making sure - therefore posts the same `event_id`, and
SignalForge acknowledges it as a duplicate rather than recording a second
deployment. That matters because "what changed just before this incident" is
answered from that timeline, and a timeline full of phantom deployments answers
it wrongly. The harness applies the same rule on its own side: re-applying the
same release with the same settings reports `"changed": false` rather than
minting a configuration version nobody made.

**A failure to announce never fails the deploy.** If SignalForge is unreachable,
refuses the credential, or has no credential configured at all, the script warns
loudly on stderr, applies the change anyway and exits 0 saying the deployment went
unannounced. Monitoring does not get to gate shipping. The one place this is
reversed is a failed *apply*: that is announced as `deployment.failed` before the
script exits non-zero, because an incident opened in the next five minutes is
very likely about it.

## Credentials

`deploy.sh` also holds `PAYSPHERE_DEPLOY_KEY`, the application's own pipeline
credential. It is deliberately not one of the three observability credentials:
SignalForge is never given a way to deploy, and the pipeline is never given a way
to read telemetry. Keeping those two directions apart is most of what makes
"wired only via public contracts" true rather than merely stated.
