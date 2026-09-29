# Releases and versioning

Imp derives release versions from Conventional Commits using semantic-release.

The existing `v0.1.0` tag is the release baseline. Historical milestone tags
are not part of version calculation.

## Version semantics

Release impact is determined from commits since the most recent `vX.Y.Z` tag:

| Commit                                      | Release impact  | Example from `0.1.0` |
| ------------------------------------------- | --------------- | -------------------- |
| `fix:`                                      | patch           | `0.1.1`              |
| `feat:`                                     | minor           | `0.2.0`              |
| `type!:` or `BREAKING CHANGE:`              | major           | `1.0.0`              |
| `docs:`, `test:`, `chore:`, `ci:`, `build:` | none by default | remains `0.1.0`      |

Pre-1.0 versions follow normal SemVer increments. In particular, a normal
`feat:` while Imp is `0.x` does **not** mean the project is ready for `1.0.0`.
For example, `feat:` after `0.1.0` produces `0.2.0`.

Do not mark a change as breaking merely because it is substantial. Use `!` or
a `BREAKING CHANGE:` footer only when the change is actually incompatible with
the supported public behavior or interfaces.

Do not choose or edit the next release number manually. Ask semantic-release:

```bash
npm run release:next
```

## Release process

semantic-release is used as the version-analysis and release-note engine. It
does not publish Imp or create the final release tag itself.

Imp deliberately keeps final tag creation local so the maintainer's signing key
does not need to exist in GitHub Actions.

From a clean `main` that exactly matches the remote-tracking main branch:

```bash
npm run release:next
npm run release:prepare
```

If there are release-relevant commits, `release:prepare`:

1. asks semantic-release for the next version and generated notes;
2. creates a local release branch named **release/vX.Y.Z**;
3. synchronizes all release version locations;
4. adds the generated release section to `CHANGELOG.md`; and
5. runs the version and diff consistency checks.

It does not commit or push anything.

Review the generated diff, run the relevant validation gates, commit the
release preparation with a non-release-producing message such as:

```text
chore(release): prepare v0.2.0
```

Push that branch and merge it through a pull request.

After the release PR is merged, update local `main` so it exactly matches
the remote-tracking main branch, then run:

```bash
npm run release:tag
```

`release:tag` re-runs semantic-release analysis and refuses to publish if the
prepared version or release notes no longer match the current commit history.
It then creates an SSH-signed annotated `vX.Y.Z` tag and pushes that tag.

The pushed version tag triggers `.github/workflows/release.yml`, which builds
and verifies the release artifacts and publishes the GitHub Release.

## Release invariants

- Release versions come from Conventional Commit semantics, not from slice
  numbers, milestone tags, or subjective estimates of change size.
- `feat:` means a minor SemVer increment. From `0.1.0`, that is `0.2.0`.
- Only an explicitly breaking change produces a major increment.
- Release preparation happens through a **release/vX.Y.Z** branch and pull
  request.
- Final `vX.Y.Z` tags are annotated and SSH-signed.
- Do not create release tags manually with plain `git tag`.
- Do not give the signing private key to GitHub Actions.
- Do not edit generated release notes between preparation and tagging.
- If release-relevant commits land after a release was prepared, prepare the
  release again rather than forcing the old tag.
