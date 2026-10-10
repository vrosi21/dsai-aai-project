# The live site

How the site runs on the server, how code and course content get there, and what to do
when something needs attention. For changing the application itself, read
[ARCHITECTURE.md](ARCHITECTURE.md); for writing content, [AUTHORING.md](AUTHORING.md).

Addresses of the server are deliberately not written in this repository. They are in three
places only: your local `.env`, the GitHub Actions secrets, and the Cloudflare Pages
project's secrets. Below, `<server>` stands for the SSH address (`user@host`).

## The shape

```
 your computer                      GitHub                         the server
 -------------                      ------                         ----------
 git push  ───────────────────────► Actions: test, build image
                                    then: deploy job ────────────► deploy app      (code)
 manage.py publish ──────────────────────────────────────────────► deploy content  (course files)


 visitor ─► https://dsai3.pages.dev
              │  Cloudflare Pages project "dsai3"            edge/_worker.js
              │  forwards every request, adding a secret header
              ▼
            the server's main Caddy                           HTTPS, certificate
              ▼
            proxy   (Caddy, in the dsai-vault project)        deploy/Caddyfile
              │  refuses requests without the secret; serves /media/
              ▼
            web     (gunicorn running Django)                 Dockerfile
              ▼
            SQLite file in a Docker volume
```

Why it has this shape:

- **Django needs a server.** Cloudflare Pages hosts files and small functions, not a
  Python process, so the application runs on the VPS.
- **A `pages.dev` address cannot point at a server.** It belongs to a Pages project. So the
  project holds one small function that passes each request on to the server
  ([edge/_worker.js](../edge/_worker.js)). The visitor only ever sees `dsai3.pages.dev`.
- **The server already had a Caddy on ports 80 and 443** (for another service). The site
  joins it with one block in that Caddy's file instead of competing for the ports.
- **Everything of ours runs in Docker, as an ordinary user.** Nothing was installed on the
  server itself and nothing needs root.

## What lives where

| What | Where | In git |
|---|---|---|
| Application code | GitHub; on the server as the image `dsai-vault:live` | yes |
| Course files (`course.yaml`, `content/`) | your computer, `courses/`; a copy on the server in `~/dsai-vault/courses/` | no |
| Source material (`source/`: slides, past papers) | your computer only | no |
| Database | the server, Docker volume `dsai-vault_data` | no, it is rebuilt from the course files |
| Images used by content | the same volume, under `media/` | no, copied by `sync_content` |
| Secrets | the server's `~/dsai-vault/.env`; GitHub Actions secrets; Cloudflare Pages secrets | never |
| Study progress | each visitor's own browser | — |

## The database

The live database is a SQLite file inside a Docker volume. It holds exactly what the local
one holds: a rendered copy of the course files, plus Django's own tables.

**Nothing writes to it directly: no person, no AI session, no script.** The only thing
that changes its content is `manage.py sync_content` running on the server, reading the
server's copy of the course files. So "putting data into the live database" always means
*getting files to the server and letting it load them*, which is what
[`publish`](#publishing-course-content) does. The admin is read-only for the same reason.

Consequences:

- A session that writes content needs no access to the server and no database password.
  The worst it can send is a bad file, and the server refuses those.
- There is nothing to back up yet. If the volume is lost: deploy, publish, done
  ([Rebuilding from nothing](#rebuilding-from-nothing)). What must not be lost is
  `courses/` on your computer.
- This changes the day accounts exist ([Next](#next-signing-in-with-faculty-accounts)):
  from then on the database holds things that exist nowhere else and needs a backup.

SQLite rather than PostgreSQL because there is one small site, almost all reads, and the
content is replaced wholesale by one writer. It runs in WAL mode so pages stay readable
while a sync writes ([config/settings.py](../config/settings.py), `DATABASES`).

## Publishing course content

```
uv run --env-file .env python manage.py publish
```

`.env` holds `DSAI_PUBLISH_SSH=<server>` (see [.env.example](../.env.example)). The command
([ingest/management/commands/publish.py](../ingest/management/commands/publish.py)):

1. validates every course under `courses/`, exactly like `sync_content --check`. Any error
   stops it and nothing is sent;
2. packs each course's `course.yaml` and `content/` folder into one archive. `source/` is
   never included;
3. pipes the archive over SSH to `deploy content` on the server, which
   - validates the new files again with the code that is live there,
   - only then replaces `~/dsai-vault/courses/`,
   - runs `sync_content`, and prints its report back to your terminal.

It always sends all courses, so the live site mirrors what you see locally: a course you
deleted disappears there too. `--dry-run` does steps 1 and 2 only.

Publishing makes the content public. It is done when the owner asks for it, not at the end
of every content session.

## Shipping code

Work on a branch, open a pull request, merge when CI is green. That is the whole routine.
[.github/workflows/ci.yml](../.github/workflows/ci.yml) has three jobs:

| Job | Runs on | Does |
|---|---|---|
| `test` | every push and pull request | ruff, `manage.py check`, migrations in step with models, the test suite |
| `image` | the same | builds the Docker image, runs `check --deploy` inside it, serves the example course from it, validates the proxy config |
| `deploy` | pushes to `main`, after both pass | sends the commit to the server |

The deploy job pipes `git archive` of the commit over SSH. On the server,
[deploy/deploy.sh](../deploy/deploy.sh) (`deploy app <commit>`) builds the image from it,
runs `migrate`, restarts the containers, re-runs `sync_content`, and finishes with
[deploy/smoke.py](../deploy/smoke.py). If any step fails the job is red and the log says
which.

Deploying by hand, from a clean checkout of the commit you want live:

```
git archive --format=tar.gz HEAD | ssh <server> "dsai-vault/bin/deploy app $(git rev-parse HEAD)"
```

### What a deploy cannot change

`compose.yaml`, `Caddyfile` and the deploy script on the server are **not** taken from the
commit being deployed. They are put there by hand:

```
git archive HEAD deploy | ssh <server> 'rm -rf ~/dsai-vault/setup && mkdir -p ~/dsai-vault/setup && tar -x -C ~/dsai-vault/setup && bash ~/dsai-vault/setup/deploy/deploy.sh install ~/dsai-vault/setup/deploy'
ssh <server> dsai-vault/bin/deploy restart
```

This is deliberate. The SSH key that GitHub Actions holds is pinned on the server to a
single command, and that command accepts a single request: "deploy this commit". A push
can therefore change the code inside the container, but not which folders, networks or
privileges the container has. Change something under `deploy/`, and the two commands above
are how it reaches the server.

## The pieces, one by one

**Cloudflare Pages project `dsai3`** — [edge/_worker.js](../edge/_worker.js). Receives every
request for `dsai3.pages.dev`, fetches the same path from `ORIGIN_URL` with the header
`X-Origin-Auth: <ORIGIN_SECRET>`, and returns the answer unchanged. Redirects are passed
back to the browser rather than followed. Both values are secrets of the Pages project.
It is uploaded with `wrangler`, not by CI, because it changes about never.

**The server's main Caddy** — not part of this project. It owns ports 80 and 443 and gets
the certificate for the origin name. Its file has one block for us, which forwards to
`dsai-vault:8080`. That works because our proxy joins its Docker network under that name
(`FRONT_NETWORK` in the server's `.env`, which `deploy install` works out by itself).

**`proxy`** — [deploy/Caddyfile](../deploy/Caddyfile). Answers 404 to anything without the
right secret header, so the site cannot be read around Cloudflare. Serves `/media/` from
the volume. Sets the `Host` header to the public name and `X-Forwarded-Proto: https`
before passing a request to Django, and asks search engines not to index anything.

**`web`** — [Dockerfile](../Dockerfile). gunicorn with two workers, as an unprivileged
user, on a read-only file system; `/data` is the only writable place. Static files are
served by WhiteNoise from inside the process, under hashed names
([config/storage.py](../config/storage.py)), so browsers keep them forever.

**Settings** — one [config/settings.py](../config/settings.py) for laptop and server. With
`DJANGO_DEBUG=0` it refuses to start without `DJANGO_SECRET_KEY`, trusts the proxy's
`X-Forwarded-Proto`, marks cookies secure, sends HSTS, and logs errors to the console.
`manage.py check --deploy` is clean and CI keeps it that way.

## Setting it up from scratch

Needed once per server. The server needs Docker, and a Caddy container that owns port 443
with its Caddyfile mounted from the host.

1. **Install the configuration and deploy the code** (from your computer):
   ```
   git archive HEAD deploy | ssh <server> 'mkdir -p ~/dsai-vault/setup && tar -x -C ~/dsai-vault/setup && bash ~/dsai-vault/setup/deploy/deploy.sh install ~/dsai-vault/setup/deploy dsai3.pages.dev'
   git archive --format=tar.gz HEAD | ssh <server> "dsai-vault/bin/deploy app $(git rev-parse HEAD)"
   ```
   The first command writes `~/dsai-vault/.env` with two freshly generated secrets.
2. **Publish the content:** `uv run --env-file .env python manage.py publish`
3. **Connect the public address:**
   ```
   bash deploy/setup-cloudflare.sh <server> dsai3 <origin-url>
   ```
   `<origin-url>` is any HTTPS name that resolves to the server; for a server without its
   own domain, `https://<label>.<ip-with-dashes>.sslip.io` works. The script adds the block
   to the main Caddy, signs you in to Cloudflare, stores the two secrets on the Pages
   project and uploads the proxy. Read its header before running it.
4. **Let GitHub Actions deploy:**
   ```
   bash deploy/setup-ci.sh <server> <owner/repo>
   ```
   It creates a deploy-only SSH key, pins it to `deploy ci` on the server, and stores it
   with the server's address and host key as Actions secrets.

On Windows, start both scripts with Git Bash by its full path. In PowerShell a bare `bash`
is the WSL launcher, which fails if no Linux is installed:

```
& "C:\Program Files\Git\bin\bash.exe" deploy/setup-cloudflare.sh <server> dsai3 <origin-url>
```

Steps 3 and 4 change things only you should change (the server's shared web server, who
may log in to it, your Cloudflare and GitHub accounts), so they are scripts for you to
read and run rather than something a session does for you.

## Day to day

| To | Run |
|---|---|
| see what is running | `ssh <server> dsai-vault/bin/deploy status` |
| read the logs | `ssh <server> dsai-vault/bin/deploy logs` (or `logs 500`) |
| restart | `ssh <server> dsai-vault/bin/deploy restart` |
| run a management command | `ssh -t <server> dsai-vault/bin/deploy manage <command>` |
| create an admin login | `ssh -t <server> dsai-vault/bin/deploy manage createsuperuser` |

**Going back to an earlier version.** The server keeps the last three images. Deploy the
earlier commit again by hand (the `git archive <commit> | ssh …` line above, with that
commit in both places). Migrations are not reversed by this; so far none has needed it.

**Changing the shared secret.** Edit `ORIGIN_SECRET` in `~/dsai-vault/.env` on the server,
run `deploy restart`, then run `deploy/setup-cloudflare.sh` again so Cloudflare sends the
new value. Between the two steps the site answers 404.

**Changing `DJANGO_SECRET_KEY`** logs everyone out and invalidates open forms; nothing else.

**Revoking the CI key.** Delete its line (it ends in `github-actions deploy (…)`) from
`~/.ssh/authorized_keys` on the server.

### Rebuilding from nothing

The database and media can be thrown away at any time:

```
ssh <server> 'cd ~/dsai-vault && docker compose down && docker volume rm dsai-vault_data'
git archive --format=tar.gz HEAD | ssh <server> "dsai-vault/bin/deploy app $(git rev-parse HEAD)"
uv run --env-file .env python manage.py publish
```

On a new server, start at [Setting it up from scratch](#setting-it-up-from-scratch).

## What is and is not protected

- The site is public for now. Every response carries `X-Robots-Tag: noindex, nofollow` and
  `/robots.txt` disallows everything, so it should stay out of search engines, but anyone
  with the address can read it.
- Source material is never published: `publish` does not pack it, and the page that links
  original files is switched off outside DEBUG.
- The server only answers requests that carry the secret, so the content is not readable
  under the server's own name.
- No secret is in the repository, in an image, or in a log. `.env` on the server is
  readable by its owner only.
- The containers run as an unprivileged user without Linux capabilities, on a read-only
  file system, and are not published on any port of the server.
- Not done yet: a Content Security Policy, rate limiting, backups (nothing to back up yet).

## Next: signing in with faculty accounts

The plan is that the site stops being public and asks for a Google sign-in with a faculty
address. Nothing of this is built. What it will take:

1. **Library.** [django-allauth](https://docs.allauth.org/) with its Google provider. It
   adds the "Sign in with Google" flow and stores users in Django's own `auth` tables.
2. **Restricting who gets in.** An allauth *adapter* that refuses any account whose
   verified e-mail does not end in the faculty domain. Passing Google the `hd` (hosted
   domain) hint narrows the account chooser, but the check that counts is the one on the
   server.
3. **Requiring it everywhere.** Django's `LoginRequiredMiddleware` makes every view
   require a login unless it is marked otherwise; the sign-in pages are exempted.
4. **Google side** (the owner does this): an OAuth client in Google Cloud Console with the
   redirect address `https://dsai3.pages.dev/accounts/google/login/callback/`. Its client
   id and secret go into `~/dsai-vault/.env`, never into git.
5. **Database.** It now holds accounts, so it gets a nightly backup: `sqlite3 .backup` to a
   file, copied off the server. The e-mail check in the settings also has to stop being
   silenced ([config/settings.py](../config/settings.py), `mail.E001`) if sign-in mails
   are ever sent.
6. **Then, optionally, progress on the server:** the `progress` app sketched in
   [ARCHITECTURE.md](ARCHITECTURE.md), so grades follow the person across devices.

Cloudflare Access could do the gatekeeping with no code, but its free plan stops at 50
people, which a faculty passes quickly.

## If you later get a real domain

Add it to Cloudflare and attach it to the Pages project as a custom domain: the proxy
keeps working unchanged. Then set `PUBLIC_HOST` in the server's `.env` to the new name and
run `deploy restart`. With a domain, a Cloudflare Tunnel could also replace the Pages
proxy and the open port entirely; that is a tidier design, not a necessary one.
