# Security policy

## Reporting a vulnerability

Please don't open a public issue for a security problem. Report it privately through
[GitHub's private vulnerability reporting](https://github.com/ndisisnd/speakingwords/security/advisories/new)
— it goes straight to the maintainers and stays closed until there's a fix.

Include what you can: what the issue is, how to reproduce it, and what an attacker could
do with it. A rough report is more useful than no report.

You'll get an acknowledgment once a maintainer sees it. Once a fix ships, you'll be
credited in the advisory unless you'd rather not be.

Private reporting has to be enabled in the repo settings before that advisory link works,
and it's off by default — if the link 404s, that's why.

## Supported versions

speakingwords is pre-1.0 and ships from `main`. Fixes land on the latest release; there is
no back-porting to older `0.x` versions. Upgrade to the newest version to get a fix.

## Scope

speakingwords runs entirely on your machine. It writes configuration into your agent — a
memory block in `CLAUDE.md` / `AGENTS.md`, a Stop-hook entry in `settings.json` /
`hooks.json`, and an installed skill directory — and in hook mode it runs `lint.py` over
your agent's own reply text on every turn. The cURL installer downloads a release tarball
and verifies its SHA-256 before unpacking; it refuses to install without a checksum unless
you pass `--insecure`.

There is no server, no network listener, and no credential handling. The realistic surface
is what the tool writes into your config, the linter it runs on reply text, and the tarball
the installer fetches and checksums.

## Disclosure

Report privately, and please hold off on publishing until a fix is out. Fixed issues are
published as a GitHub advisory that credits the reporter.
