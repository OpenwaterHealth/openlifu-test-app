# Code signing

Release builds of the Windows package are Authenticode-signed with
Openwater's SSL.com **EV code-signing certificate** (Open Water Internet,
Inc.), the same certificate the Open-Motion apps ship with. The private key
lives in SSL.com's **eSigner** cloud HSM and never touches the build runner:
CI installs **eSigner CKA**, which loads the certificate into
`Cert:\CurrentUser\My`, and `signtool` signs against its thumbprint while the
signature itself is computed in SSL.com's cloud.

**Every signing is metered.** Do not add signing to more build paths without
checking the eSigner plan first.

## When CI signs

| Build | Signed | Saved to |
|---|---|---|
| `X.Y.Z` tag (full release) | yes | GitHub Release asset + workflow artifact |
| `X.Y.Z-rc.N` tag (release candidate) | yes | GitHub Release (pre-release) asset + workflow artifact |
| `workflow_dispatch` on one of those tags | yes | same as the tag build |
| pushes to `main` / `next`, `workflow_dispatch` on a branch (dev builds) | no | workflow artifact only |

The decision is the `SIGN` output of the "Determine version tag" step in
[`release-build.yml`](../.github/workflows/release-build.yml).

## What gets signed

`TestApp.exe` and `TestApp_console.exe` at the top of the bundle, before it
is zipped: **2 signings per release build**. The Qt and Python DLLs under
`_internal\` are third-party and ship as their vendors signed them (or not).
A zip cannot carry an Authenticode signature, so the release zip itself is
not signed; the signed executables inside it are what Windows checks.

[`scripts/sign.ps1`](../scripts/sign.ps1) signs with an RFC 3161 timestamp
from `http://ts.ssl.com` (so the signature stays valid after the certificate
expires) and verifies each file afterwards. A release build fails, and
nothing is published, if:

- the eSigner secrets are not available to the repo,
- a signature does not verify as `Valid`, is not from the loaded
  certificate, or has no timestamp, or
- the signed `TestApp_console.exe --help` does not run (the smoke test
  that also runs on every dev build).

## One-time setup

The eSigner credentials are org secrets shared with the Open-Motion repos:
`ES_USERNAME`, `ES_PASSWORD`, `ES_TOTP_SECRET`. They are scoped to selected
repositories, so an org admin has to add this repo to each:

```bash
REPO_ID=$(gh api repos/OpenwaterHealth/openlifu-test-app --jq .id)
for s in ES_USERNAME ES_PASSWORD ES_TOTP_SECRET; do
  gh api -X PUT "orgs/OpenwaterHealth/actions/secrets/$s/repositories/$REPO_ID"
done
```

Background on the account itself (eSigner enrollment, the TOTP secret,
disabling Malware Blocker for CKA signing) is in Open-Motion's
`openmotion-bloodflow-app/docs/SIGNING.md`.

There is no separate signing dry run: the first rc tag after setup is the
test. A dispatch on an existing tag rebuilds and re-uploads that tag's
release, so do not use an old tag for it.

## Verifying a release

```powershell
Get-AuthenticodeSignature .\TestApp\TestApp.exe | Format-List Status, SignerCertificate, TimeStamperCertificate
# Status must be Valid and the signer "Open Water Internet, Inc."
```

The certificate's SHA-1 thumbprint is
`ADFCD7CB6A7900A204F91EDF9E5ADADB5A8C1AB4` (valid 2026-08-05 to 2027-11-06).
