# Security policy

CrisisWeave handles crisis information and may be deployed around sensitive operational data. Security reports should therefore avoid creating additional exposure.

## Reporting a vulnerability

Do **not** open a public issue containing credentials, private survivor information, precise private locations, exploitable request details or a working exploit against a live deployment.

Use GitHub's private vulnerability reporting / Security Advisory flow for this repository when available. If that private channel is unavailable, contact the maintainer through a private contact method published on the maintainer's GitHub profile without posting the sensitive details publicly.

Include the affected revision, a concise impact description, reproducible steps using synthetic data where possible, and any safe mitigation you already know.

## Supported code

Security fixes target the current `main` revision and component revisions promoted by the `davidmariscalf/CrisisWeave` umbrella lock. Older unpinned revisions are best-effort only.

## Operational incidents

If a live deployment may be compromised, operators should prioritize stopping unsafe exposure, revoking credentials, preserving audit evidence and following their incident-response runbook. CrisisWeave is not an emergency authority and must not be the sole source for safety-critical decisions.
