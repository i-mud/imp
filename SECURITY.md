# Security policy

## Supported versions

Imp is currently an alpha project.

| Version | Supported |
| ------- | --------- |
| 0.2.x   | Yes       |
| 0.1.x   | No        |
| Older   | No        |

Security fixes may require upgrading to the newest available release.

## Reporting a vulnerability

Do **not** open a public GitHub issue for a suspected security vulnerability.

Use GitHub's private vulnerability reporting flow from the repository's
**Security -> Report a vulnerability** page.

If private vulnerability reporting is unavailable, contact the maintainer at
`tuataur@googlemail.com`.

Include, when possible:

- the affected Imp version or commit;
- the affected component;
- reproduction steps;
- security impact;
- any proof-of-concept material needed to understand the issue; and
- suggested remediation, if known.

Do not include unrelated secrets or personal data.

## Scope

Relevant Imp security boundaries include:

- desktop connection handling and local-node supervision;
- Managed and External SSH integration;
- authenticated Direct WSS;
- pairing-token handling;
- the loopback Imp node and gateway;
- TinyFugue and Mudlet capture/action boundaries;
- shared GMCP normalization and protocol validation; and
- server installation and update behavior.

Vulnerabilities that exist entirely in an upstream dependency or MUD client
itself should normally be reported to that upstream project unless Imp
introduces or materially worsens the issue.

## Disclosure

Please allow reasonable time for investigation and remediation before public
disclosure.

The project will coordinate disclosure when a vulnerability affects released
versions of Imp.
