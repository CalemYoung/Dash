# Security policy

## Supported versions

Only the latest release of Dash receives security fixes. Dash checks for
updates when it starts (unless you turn that off), and "Check for Updates..."
in the tray menu installs the latest version.

## Reporting a vulnerability

Please report security problems privately, not in a public issue:

1. Go to the [Security tab](https://github.com/CalemYoung/Dash/security) of
   the repository.
2. Choose **Report a vulnerability** to open a private security advisory.
3. Describe the problem, the Dash and Windows versions you used, and the
   steps to reproduce it.

You should get a reply within a week. Once a fix is released, the advisory
is published with credit to you, unless you would rather stay anonymous.

## How updates are delivered

The in-app updater is the part of Dash most relevant to security:

- It asks the GitHub API over HTTPS
  (`https://api.github.com/repos/CalemYoung/Dash/releases/latest`) whether a
  newer release exists. Turn off "Check updates at startup" in Settings
  to stop the automatic check.
- Nothing is downloaded or installed until you choose "Install and Restart".
- The installer and its `.sha256` checksum file are downloaded over HTTPS
  from the GitHub release. Redirects that would downgrade to HTTP are
  refused.
- The installer is run only if its SHA-256 hash matches the checksum file
  published with the release. A release without a checksum can only be
  opened in the browser.
- Downloaded installers are kept in `%TEMP%\Dash\updates` and removed when
  Dash is uninstalled.

Releases are built from tagged commits by the
[Release workflow](.github/workflows/release.yml) on GitHub's Windows
runners. Installers are not code signed, so Windows SmartScreen may warn
the first time you run one.
