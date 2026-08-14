# Security policy

AGIWiki is alpha software for local personal factual memory. Security fixes are currently
made on the latest `main`; no older release line is guaranteed support before 1.0.

## Reporting a vulnerability

Use GitHub's **Security → Report a vulnerability** private reporting form for this
repository. Do not put exploit details, source documents, tokens, private paths, or personal
data in a public issue. If private reporting is temporarily unavailable, open a public issue
containing only a request for a private contact channel.

Include the affected version/commit, platform, impact, and a minimal reproduction that uses
synthetic data. Please allow the maintainer time to reproduce and prepare a fix before public
disclosure.

## Security boundary

The supported threat model is documented in `docs/security-model.md`. AGIWiki does not
protect against a malicious OS administrator or a compromised user account, does not prove
Entry truth, and cannot recognize every secret embedded in prose.
