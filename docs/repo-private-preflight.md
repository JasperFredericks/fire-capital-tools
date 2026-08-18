# Pre-flight: making this repository private

**Written 2026-08-18. Nothing was changed. This is reconnaissance so the
click is safe.**

The plan is to make `firecapitaltools/fire-capital-tools` private and then
store Paresh's KoboToolbox reference files in it. **Michelle has to do the
visibility change** — see "who can do what" below, which was verified
rather than assumed.

This is the same class of change as the ownership transfer, which broke
Railway's deploy connection for about an hour and went unnoticed. The
point of this document is that it does not go unnoticed twice.

---

## What was verified

| fact | how |
|---|---|
| Repo is currently **public** | Anonymous `GET /repos/...` returns 200, `private: false` |
| Railway deploys from a **GitHub repo connection**, not an image | Railway GraphQL: `serviceInstances.source = {repo: "firecapitaltools/fire-capital-tools", image: null}` |
| The connection is **working right now** | Two of Beckett's commits are QUEUED and BUILDING as of writing |
| **We do not have admin on the repo** | `GET /repos/...` returns `permissions: {admin: false, maintain: false, push: true, triage: true, pull: true}` |

That last row is the one that matters procedurally: **write access is not
enough to change visibility.** Michelle, as owner, has to do it.

### Deploy baseline — compare against this afterwards

```
2026-08-18T23:05:40  QUEUED    8c104f4b  master  8a762498
2026-08-18T22:54:11  BUILDING  39f84a77  master  5935317c
2026-08-18T18:44:55  SUCCESS   3cd6c2bf  master  5a42d526
```

After the change, push a trivial commit and confirm a **new deployment
appears with a new id and reaches SUCCESS**. If no new deployment appears
at all, the webhook link is broken — that is the failure mode that hid
for an hour last time, because the previously-deployed version keeps
serving happily and nothing looks wrong from outside.

---

## What could NOT be determined, and who can

**I could not establish which mechanism Railway uses to reach the repo,
and therefore cannot promise the connection survives.** Every endpoint
that would answer it is permission-gated:

| probe | result |
|---|---|
| Railway `githubRepos` | `Not Authorized` — needs a GitHub-linked session scope the CLI token lacks |
| Railway `githubWritableScopes` | `Not Authorized` |
| `GET /repos/.../installation` | 401 — needs a JWT signed by the App itself, not a user token |
| `GET /repos/.../hooks` | 404 — needs admin (GitHub returns 404, not 403, to avoid confirming existence) |
| `GET /orgs/firecapitaltools/installations` | 404 — needs org admin |
| `GET /user/installations` | 403 — needs a token authorized to a GitHub App |

**Michelle can settle it in about thirty seconds** from
`https://github.com/settings/installations` (or the organisation's
equivalent under Settings → GitHub Apps):

- **If "Railway" appears as an installed GitHub App with this repository
  granted** — either "All repositories" or explicitly selected — then
  going private is very likely safe. A GitHub App's access is granted by
  installation and is not inferred from visibility, so flipping the repo
  to private does not by itself revoke it.
- **If Railway does NOT appear**, the connection is running on something
  that depends on the repo being publicly readable, and **going private
  will break deploys**.

I am deliberately not asserting which of these is true. The transfer
breaking the connection once is weak evidence for an App installation
(transfers do break App installations, because the new owner has to
install the App), but weak evidence is not a basis for telling somebody
their deploys are safe.

---

## Order of operations

1. **Check the installed-Apps page first** (above). It is cheaper than
   recovering.
2. Make the repo private.
3. **Immediately** push a trivial commit — a whitespace change to a
   comment is fine.
4. Confirm a new deployment appears and reaches SUCCESS, comparing
   against the baseline above.
5. Only once deploys are confirmed working, add the Kobo files.

Do not do step 5 before step 4. If the connection needs re-authorizing,
you want to be debugging one change and not two.

---

## Recovery, if deploys stop

The symptom is silent: the site keeps serving the last successful build,
and no new deployment appears for a push.

1. **Railway dashboard → the `fire-capital-tools` service → Settings →
   Source.** If it shows an error, or the repo no longer resolves,
   disconnect and reconnect the repository. Railway will prompt to
   install or re-authorize its GitHub App and to grant access to the now
   private repository.
2. **Grant the App access to the specific repo.** If the installation is
   set to "Only select repositories", a newly private repo may not be in
   the selected set. Add it explicitly.
3. **Re-check the source afterwards** — it must read
   `firecapitaltools/fire-capital-tools`, not a fork or the old owner:
   the CLI shows it via `railway status`, and the GraphQL query in this
   repo's history returns `serviceInstances.source.repo`.
4. **Redeploy the current commit** once reconnected, then verify by
   container code check — hash a known file in `/app` against the
   committed blob — rather than by trusting the dashboard. That is the
   check that caught the transfer breakage.

Environment variables, the `/data` volume and the database contents are
**not** affected by a visibility change. Only the build trigger and the
source fetch are at risk.
