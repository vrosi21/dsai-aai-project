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
| Course files (`course.yaml`, `content/`) | your computer, `courses/`; a copy on the server in `~/dsai-vault/courses/` | not in this repository: in [their own](#course-files-and-the-educator-kit) |
| Source material (`source/`: slides, past papers) | your computer only | no |
| Database: course content | the server, Docker volume `dsai-vault_data` | no, it is rebuilt from the course files |
| Database: accounts, study progress, requests, educators, hand-ins, the switches for a course | the same file | no, and nothing rebuilds it: it is backed up |
| Courses as handed in, unpacked | the same volume, under `handins/` | no, rebuilt from the hand-ins in the database |
| Images used by content | the same volume, under `media/` | no, copied by `sync_content` |
| Secrets | the server's `~/dsai-vault/.env`; GitHub Actions secrets; Cloudflare Pages secrets | never |
| A working copy of each person's progress | that person's browser | — |

## The database

The live database is one SQLite file inside a Docker volume. It holds two kinds of thing,
and they could not be more different:

| | Course content | People |
|---|---|---|
| Tables | `catalog_*` | `auth_user`, `socialaccount_*`, `accounts_enrollment` (the courses each person chose), `progress_*`, sessions |
| Written by | `manage.py sync_content`, from the course files | the site itself: a sign-in creates the person, [progress/sync.py](../progress/sync.py) stores what they did |
| If lost | deploy and publish: it is all back | gone, unless there is a backup |

**No person, AI session or script writes to it directly.** Course content gets in only as
files that the server loads ([`publish`](#publishing-course-content)). People's rows get
in only through people using the site. The admin is read-only for course content; for
people it offers a few deliberate actions (see [Admin](#admin)).

Consequences:

- A session that writes content needs no access to the server and no database password.
  The worst it can send is a bad file, and the server refuses those.
- **The database must be backed up**, because accounts and progress exist nowhere else
  (a browser holds a copy of its owner's progress, but that is not a backup).
  `deploy backup` makes a consistent copy in `~/dsai-vault/backups/` and keeps the newest
  fourteen. Have it run every night, once (it adds one line to the server user's crontab):
  ```
  ssh <server> dsai-vault/bin/deploy schedule-backup
  ```
  A copy on the same disk does not survive the disk, so fetch one to your computer now and
  then: `scp <server>:dsai-vault/backups/<newest file> .`
- Re-importing a course never touches anyone's progress. Progress rows name an item by the
  course's slug and the item's id, not by a database key, which is one more reason ids
  must never be renamed.

SQLite rather than PostgreSQL because it is one small site with few writers. It runs in
WAL mode so pages stay readable while something writes
([config/settings.py](../config/settings.py), `DATABASES`). The day many people study at
the same minute and writes start to queue, PostgreSQL is a settings change plus a
`dumpdata` / `loaddata` of the people tables; the content tables just get re-synced.

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

Publishing shows the content to everyone who can sign in. It is done when the owner asks
for it, not at the end of every content session.

## Course files, and the educator kit

Three repositories, each with one job:

| Repository | Holds | Who writes to it |
|---|---|---|
| this one | the application | you, through pull requests |
| the content repository (`courses/` on your computer is its checkout; private) | every course's `course.yaml` and `content/`, grouped in `semester-N/` folders | you alone |
| the educator kit (`educator-kit/` here is its checkout; **public**) | instructions for an AI assistant, the authoring docs, three tools. **No course** | you alone; educators clone it and pull |

Courses are grouped in `semester-N/` folders for people's sake. A course's address is
still its own folder's name, `publish` sends it as `<slug>/`, and the server keeps the
courses side by side as before. `source/` is ignored in the content repository: slides and
past papers are the lecturers' and are never committed anywhere.

**How an educator's work reaches the site.** The site's guide (`/educators/guide/`) and
the kit's README say it to them step by step. In short:

1. They clone the kit, download their course's files under Educators, and unpack
   them into the kit with `tools/unpack.py`, which commits that state in their copy.
   Their course is tracked by git on their computer only; pushing is switched off.
2. Their assistant writes; they check; `tools/pack.py` makes one zip.
3. They upload that zip on their course's page. They may add, change and remove. The
   site takes it in if it is built on the version the site has and the full validation
   passes; one that removes or rewrites anything is first shown back to them and needs a
   tick. Then it is **live at once**. Otherwise it is refused with the reason and the
   steps to take, and nothing changes.
4. You are notified (the bell, and the bot if it is set up; more urgently when something
   was removed), and the course's history on their page gains a step.

**Backups of a course.** Every hand-in keeps the whole course as it stood after it, in
the database, which is backed up with everything else. Each step can be downloaded (by
the course's educators on their page, by you in the admin), and handing an earlier one
in again brings it back. Your own course files, and their git history, are a second copy
that no hand-in can touch.

**What you do about a hand-in.** Nothing, for it to be live. Two things remain yours:

- *Taking it into your course files.* The site shows the course as changed there (a
  zip handed in, or edits made in the editor), from a folder of its own; your files do
  not have those changes until you fetch them. All of them at once, as git history:
  ```
  uv run --env-file .env python manage.py take_in --check     what there is to take
  uv run --env-file .env python manage.py take_in             take it
  ```
  It asks the server for every step your files lack (over the SSH connection `publish`
  uses; on the server it runs `export_steps`, which only reads) and makes each one a
  commit in `courses/`: the person who made the step as author, by name, with the
  time they made it, and what it did as the message (`rl: 1 new file, 12 lines added`).
  `git log` in `courses/` is then the course's history as it happened on the site. A
  year that was started on the site gets its folder made. It commits and nothing more:
  it does not push, load or publish. Read the log, `sync_content`, `publish`.

  It takes a course only if your files are the version its steps began from, and its
  folder has no uncommitted changes. A course you changed yourself meanwhile is left
  alone and named. For that one, or to take a single step by hand: in the admin, under
  Hand-ins, download the newest one for the course (each holds the whole course as it
  stood) and run
  ```
  uv run python manage.py import_course rl-handin-12.zip
  ```
  It validates the zip. If you have not changed that course yourself since you last
  published it, your files simply become the educators' version: what they added,
  changed and removed. If you have, it takes what the zip adds and stops at any file
  that differs otherwise, for you to decide (`--theirs`, `--mine`). Read the diff,
  `sync_content rl`, commit in `courses/`, and `publish`. Either command leaves a small
  file in the course folder, `.merged`, saying which step your files now include
  (`take_in` commits it with the last step; after `import_course`, commit it too). `publish` sends it along, and the site then marks the hand-in merged and drops
  its own copy. If you publish without doing this, nothing is lost: the site keeps
  showing the hand-in, and the publish report says which courses lack hand-ins.
- *Taking it off the site.* Hand-ins in the admin, tick it, **Take off the site**. It and
  every later hand-in for that course are withdrawn and the course is loaded again as it
  was. The educators of the course are told under their bell. Their own copies still
  have the work, so say what was wrong before they hand in again. (An educator can take
  back their own newest hand-in themselves.)

When you change a course in your own files and publish, educators get the new version
with their next download, and a hand-in built on the old one is refused until they do.

**The kit.** It is generated from this repository: its texts are in
[ingest/educator_kit/](../ingest/educator_kit/), its docs are `docs/`, its tools run
copies of the importer's own Django-free modules, and its versions are listed in
[desk/kit.py](../desk/kit.py). To release a change:

1. Edit the sources, and add a version at the top of `desk/kit.py`. Merge.
2. `uv run python manage.py export_authoring educator-kit`, then commit and push in
   `educator-kit/`.
3. In the admin, Kit announcements, add the version. Educators see a notice on their
   page and are e-mailed to run `git pull`. Do this last: it tells people to pull.

The kit's repository is public, so an educator needs no invitation and nobody but you
can push to it. It contains no course and nothing secret; check that this stays true
before each push (`git status`, `git diff` in `educator-kit/`).

## Shipping code

Work on a branch, open a pull request, merge when CI is green. That is the whole routine.
[.github/workflows/ci.yml](../.github/workflows/ci.yml) has three jobs:

| Job | Runs on | Does |
|---|---|---|
| `test` | every push and pull request | ruff, `manage.py check`, migrations in step with models, the test suite |
| `image` | the same | builds the Docker image, runs `check --deploy` inside it, serves the example course from it, validates the proxy config |
| `deploy` | pushes to `main`, after both pass | sends the commit to the server |

**When a run is red for no reason of yours.** The `image` job downloads two images from
public registries, and those refuse anonymous downloads from GitHub's shared addresses now
and then (`toomanyrequests: Rate exceeded`).
[.github/scripts/pull-image.sh](../.github/scripts/pull-image.sh) fetches them first, trying
three registries several times, so this should no longer fail a run. If a run on `main`
does fail on something that has nothing to do with the code, the deploy was skipped:
open the run on GitHub and press **Re-run failed jobs**, and the deploy follows.

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
That block is the one part of this site kept in somebody else's file; see "The site
answers 525" under [Day to day](#day-to-day) for what happens when it is lost.

**`proxy`** — [deploy/Caddyfile](../deploy/Caddyfile). Answers 404 to anything without the
right secret header, so the site cannot be read around Cloudflare. Serves `/media/` from
the volume. Sets the `Host` header to the public name and `X-Forwarded-Proto: https`
before passing a request to Django, and asks search engines not to index anything. It
turns away a request larger than 30 MB, and tells browsers the site uses neither camera,
microphone, location, payments nor USB.

**`web`** — [Dockerfile](../Dockerfile). gunicorn with two workers, as an unprivileged
user, on a read-only file system; `/data` is the only writable place. Its line per
request leaves out the query string and the referring page. Static files are
served by WhiteNoise from inside the process, under hashed names
([config/storage.py](../config/storage.py)), so browsers keep them forever.

**Settings** — one [config/settings.py](../config/settings.py) for laptop and server. With
`DJANGO_DEBUG=0` (which is also what it assumes once `DJANGO_ALLOWED_HOSTS` is set) it
refuses to start without `DJANGO_SECRET_KEY`, trusts the proxy's
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
5. **Switch on signing in:** create the OAuth client and run `deploy/setup-google.sh`
   ([Accounts](#accounts) has the steps). Until then the site shows its landing page and
   says that signing in is not set up.
6. **Schedule the backup:** `ssh <server> dsai-vault/bin/deploy schedule-backup`.

On Windows, start the scripts with Git Bash by its full path. In PowerShell a bare `bash`
is the WSL launcher, which fails if no Linux is installed:

```
& "C:\Program Files\Git\bin\bash.exe" deploy/setup-cloudflare.sh <server> dsai3 <origin-url>
```

Steps 3 to 6 change things only you should change (the server's shared web server, who
may log in to it, your Cloudflare, GitHub and Google accounts), so they are scripts for
you to read and run rather than something a session does for you.

## Day to day

| To | Run |
|---|---|
| see what is running | `ssh <server> dsai-vault/bin/deploy status` |
| read the logs | `ssh <server> dsai-vault/bin/deploy logs` (or `logs 500`) |
| restart | `ssh <server> dsai-vault/bin/deploy restart` |
| back up the database | `ssh <server> dsai-vault/bin/deploy backup` |
| run a management command | `ssh -t <server> dsai-vault/bin/deploy manage <command>` |
| let yourself into `/admin/` | sign in on the site once, then `ssh <server> dsai-vault/bin/deploy manage make_admin you@example.com` |

**Going back to an earlier version.** The server keeps the last three images. Deploy the
earlier commit again by hand (the `git archive <commit> | ssh …` line above, with that
commit in both places). Migrations are not reversed by this; so far none has needed it.

**The site answers 525 for everyone** ("SSL handshake failed", or people report an SSL
error). The certificate visitors see is Cloudflare's and is fine; what fails is
Cloudflare reaching the server. The main Caddy answers for the origin name only because
its Caddyfile has a block for it, and that file belongs to another project on the server.
When that project is deployed again, its own copy of the file replaces it, the block is
gone, Caddy has no certificate to show for the name, and the handshake fails. Nothing in
this project is broken when that happens (it did, on 13 October 2026). `deploy status`
says so on its `front:` line, and every deploy prints the same line. To put the block
back:

```
ssh <server> 'bash -s' < deploy/restore-front.sh
```

It appends the block to the file as it is now, never replaces the file, keeps a copy,
applies the change only if Caddy accepts it, reloads without a restart, and asks another
site of that Caddy the same thing before and after to show it was not disturbed. The
lasting cure is in the other project: the Caddyfile it ships has to contain this site's
block too.

**Changing the shared secret.** Edit `ORIGIN_SECRET` in `~/dsai-vault/.env` on the server,
run `deploy restart`, then run `deploy/setup-cloudflare.sh` again so Cloudflare sends the
new value. Between the two steps the site answers 404.

**Changing `DJANGO_SECRET_KEY`** logs everyone out and invalidates open forms; nothing else.

**Being told when something arrives.** When a request or an educator application is
stored, the site posts a short message to the owner's notification bot
([desk/notify.py](../desk/notify.py)). It needs two values in the server's `.env`, the
bot's address (https) and its token:

```
ssh <server> dsai-vault/bin/deploy setenv BMO_NOTIFY_URL
ssh <server> dsai-vault/bin/deploy setenv BMO_NOTIFY_TOKEN
ssh <server> dsai-vault/bin/deploy restart
```

Each `setenv` waits for the value on standard input: type or paste it and press Enter, so
it is never part of a command line. The two variables reach the container through
`compose.yaml`, so the first time this is set up the files under `deploy/` must be put on
the server too ([What a deploy cannot change](#what-a-deploy-cannot-change)). Without the
two values everything still works: requests are stored and shown in the admin, nobody is
told, and the log says so once at start-up. A message never contains a name, a phone
number or what was written.

**E-mail to educators.** The site writes to educators about their role: an accepted
application, a new version of the kit, a hand-in taken off the site
([desk/mail.py](../desk/mail.py)). It needs a mail server: any provider's SMTP details.
Set each value with `deploy setenv`, then restart:

```
ssh <server> dsai-vault/bin/deploy setenv DSAI_EMAIL_HOST
ssh <server> dsai-vault/bin/deploy setenv DSAI_EMAIL_USER
ssh <server> dsai-vault/bin/deploy setenv DSAI_EMAIL_PASSWORD
ssh <server> dsai-vault/bin/deploy setenv DSAI_EMAIL_FROM
ssh <server> dsai-vault/bin/deploy restart
```

The port is 587 with STARTTLS unless `DSAI_EMAIL_PORT` says otherwise. Like the bot's
values these reach the container through `compose.yaml`, so the files under `deploy/`
must be on the server first. Without a host nothing is sent and nothing fails. The
privacy page says who is written to and about what; keep it true if that grows.

With a mail server and `DSAI_CONTACT_EMAIL` both set, the site also writes to that
address whenever its bot is told something: a request, an application, a hand-in. The
message says the same as the bot's, and carries the same link to the item in the admin.
With a Gmail mailbox the host is `smtp.gmail.com`, the user is the address, and the
password is an *app password* made in that Google account (Security, 2-Step
Verification, App passwords), never the account's own password.

**Telling people something.** In the admin, under Requests and educators, Announcements:
write a title, a few lines and, if you like, a link, and choose who it is for: students
and educators, or educators only, of every course or of one. For one course, "students"
are the people who have it on their dashboard. Saving it puts it under their bells at
once, and says how many that was. Change it there to correct every copy; delete it to
take it back. Who it is for cannot be changed afterwards. It is not e-mailed.

**Opening a course, and who writes it.** In the admin, under Courses: tick the course and
choose an action. *Open to students* and *Close* decide whether students see it at all;
a course whose file says `enabled: false` stays hidden until it is opened here. *Ask for
an educator* and *Someone writes it* decide what its page says while it is empty.
*Clear my switches* goes back to what the course file says. Applications to write a
course are under Requests and educators; accepting one makes that person its educator.

**Revoking the CI key.** Delete its line (it ends in `github-actions deploy (…)`) from
`~/.ssh/authorized_keys` on the server.

### Restoring a backup

```
ssh <server> 'dsai-vault/bin/deploy restore ~/dsai-vault/backups/<file>'
uv run --env-file .env python manage.py publish
```

The first line stops the site for a few seconds and swaps the database. The second reloads
the course content, in case it changed since the backup was made.

### Rebuilding from nothing

**This erases every account and all progress.** Only the course content comes back. Make
a backup first unless losing them is the point.

```
ssh <server> 'cd ~/dsai-vault && docker compose down && docker volume rm dsai-vault_data'
git archive --format=tar.gz HEAD | ssh <server> "dsai-vault/bin/deploy app $(git rev-parse HEAD)"
uv run --env-file .env python manage.py publish
```

A browser that was signed in before notices that the server has forgotten it and sends its
own copy of that person's progress again at the next sign-in, so people who come back get
their progress back from their own device.

On a new server, start at [Setting it up from scratch](#setting-it-up-from-scratch).

## What is and is not protected

- Every page needs a sign-in, except the landing page. Any Google account is accepted, so
  this keeps out crawlers and passers-by, not a determined person. Restricting it to
  certain addresses is described under [Accounts](#accounts).
- Images used by course content (`/media/…`) are served by the proxy without a sign-in.
  Their names are long hashes, so they cannot be guessed, but anyone given the address of
  one can open it.
- Every response carries `X-Robots-Tag: noindex, nofollow` and `/robots.txt` disallows
  everything.
- Source material is never published: `publish` does not pack it, and the page that links
  original files is switched off outside DEBUG.
- The server only answers requests that carry the secret, so nothing is readable under the
  server's own name.
- `/privacy/` says in plain words what is stored and how to remove it, and `/terms/`
  what the site is for, that its material can be wrong, and how to ask for something to
  be removed. Both are public, and Google's consent screen links to both.
  `DSAI_CONTACT_EMAIL` in the server's `.env` puts a link for questions and removal
  requests on them; without it the terms can only say "tell the person who runs the
  site". The address is where the link leads and is not written out on the page. Set it
  with `deploy setenv DSAI_CONTACT_EMAIL`; a restart, or the next deploy, applies it.
- About each person the site stores what Google hands over at sign-in (name, e-mail
  address, Google's id for the account), which courses they chose, their study
  progress, whether they have taken the tour, and what they sent themselves: requests,
  and an application to write a course with its phone number. No passwords. A person
  can delete all of it from Settings; deleting the user in the admin does the same.
- A request about a course is shown to that course's educator without the name of who
  sent it. Applications, with their phone numbers, are in the admin only. The
  notification sent to the owner's bot carries none of it.
- A course the owner has not opened answers 404 to everyone but staff, and is left out
  of every list and of search.
- An educator can change their own course and nothing else. What they upload is one
  zip, checked before anything is written: paths, kinds of file, sizes, that it is built
  on the version the site has, that anything it removes was confirmed, and the full
  validation. It is unpacked only under the data volume, never into the published course
  files, and the version before it stays in the database. One hand-in at a time per
  course, twenty a day per person.
- Images from course content are served with a policy that lets them run nothing when
  opened on their own, and SVG files that carry a script are refused at the door.
- The site sends e-mail only to educators, one message per person, and to its own
  contact address when something arrives (without who sent it or what it says). Both
  only if a mail server is configured.
- Asking, applying and the educators' page are rate limited like the rest, and one
  account can send ten requests a day.
- A request larger than its page accepts is refused before it is read: 2.5 MB anywhere,
  26 MB where a signed-in person hands in a course
  ([config/bodylimit.py](../config/bodylimit.py)), and 30 MB at the proxy whatever the
  page. Without that, any visitor could have the server write an upload of any size to
  its temporary folder, which is memory.
- No secret is in the repository, in an image, or in a log. `.env` on the server is
  readable by its owner only.
- What the containers log is one line per request (time, page, answer, size, duration,
  kind of browser) and Django's warnings. Not the visitor's address, not who was signed
  in, and not what follows the `?` of an address, so no search term and no sign-in code.
  A failed e-mail is logged by its kind, never with the address. Docker keeps at most
  50 MB of it per service ([deploy/compose.yaml](../deploy/compose.yaml)). The privacy
  page says exactly this, and a test holds the image's log format to it.
- Caddy writes a line about every request it could not pass on, and puts the request in
  it: all headers (so the shared secret and the visitor's address) and the address line
  with its query. That happens for a few seconds at every restart, and it was found in
  this server's log. [deploy/Caddyfile](../deploy/Caddyfile) now leaves the request out
  of those lines. The server's main Caddy writes the same kind of line when *our* proxy
  is the one restarting; it is not part of this project, so the same `log` block has to
  be put into its global options by hand. Until then the privacy page names that case.
- The main Caddy's block for this site (`setup-cloudflare.sh` adds it) has
  no `log` line, and Caddy writes no access log without one; if you add one, that log
  has the visitor's address and the secret header in it, and the privacy page is wrong.
- Debug mode cannot come on by omission: it is off wherever `DJANGO_ALLOWED_HOSTS` is
  set, unless `DJANGO_DEBUG=1` says otherwise.
- The containers run as an unprivileged user without Linux capabilities, on a read-only
  file system, and are not published on any port of the server.
- Every page carries a Content Security Policy: scripts, styles, fonts and images load
  from the site itself and from nowhere else, and no other site can frame it.
- The admin has no password form on the server. `/admin/` leads a visitor to the
  landing page; only a staff account signed in with Google gets further.
- One account can store a bounded amount of progress (`LIMITS` in
  [progress/sync.py](../progress/sync.py)), so signing in is not a way to fill the disk.
- Saving progress, search and starting a sign-in are rate limited per account, or per
  visitor address when signed out (`RATE_LIMITS` in
  [config/settings.py](../config/settings.py)). The limits sit far above what a class of
  fifty does, including all of them signing in from one campus address in the same
  minute; they exist to stop a loop, not to manage load. The visitor's address is the
  `CF-Connecting-IP` header, which Cloudflare sets and the proxies pass on.
- Not done yet: off-server backups.

## Accounts

People sign in with Google, through [django-allauth](https://docs.allauth.org/). The site
never sees or stores a password. The first sign-in creates the person as an ordinary
Django user; their progress is then saved to the server and follows them between devices
([ARCHITECTURE.md](ARCHITECTURE.md#accounts-and-progress) explains how).

### Creating the Google OAuth client (once)

In [Google Cloud Console](https://console.cloud.google.com/), signed in with the Google
account that should own this:

1. Create a project (any name, for example "DSAI Vault").
2. **APIs & Services → OAuth consent screen** (it opens "Google Auth Platform"): user type
   **External**; app name, your e-mail as support and developer contact. Scopes: leave
   the defaults (`email`, `profile`, `openid`).
3. **Branding**: Google will not let the app be published without these. The logo is
   optional: the file to upload is `static/brand/logo-512.png`. With a logo the console
   says "Your app requires verification". That is about the branding only: until Google
   has verified it (Verification Center; it asks you to prove you control the home
   page's domain and takes days), its sign-in screen does not show the app's name and
   logo. Signing in works regardless, and the user cap shown on the Audience page does
   not apply, because the site asks for no sensitive scopes. Submit it once the site is
   live: the reviewers open the home page and the privacy link.
   ```
   Application home page            https://dsai3.pages.dev/
   Application privacy policy link  https://dsai3.pages.dev/privacy/
   Application terms of service     https://dsai3.pages.dev/terms/
   Authorized domains               dsai3.pages.dev
   ```
4. **Audience → Publish app** ("In production"). While it is in "Testing", only the
   test users you list there can sign in.
5. **Clients → Create client** (the same as Credentials → Create credentials → OAuth
   client ID), type **Web
   application**. Under **Authorised redirect URIs** add exactly:
   ```
   https://dsai3.pages.dev/accounts/google/login/callback/
   http://localhost:8000/accounts/google/login/callback/
   ```
6. **Download JSON** for the new client.

Then, from this folder:

```
bash deploy/setup-google.sh <server> <path to the downloaded JSON>
```

It stores the client id and secret in the server's `.env` (and in your local `.env`, so
Google sign-in also works on `localhost`), installs the current `deploy/` configuration
and restarts. Run it **before** the code with sign-in goes live; in the other order the
site is closed until you do.

### Who may sign in

Anyone with a Google account. To narrow it later (the original plan was faculty addresses
only), write an allauth *adapter*: a small class whose `pre_social_login` refuses a
sign-in unless the verified e-mail ends in the wanted domain, named in
`SOCIALACCOUNT_ADAPTER`. Passing Google the `hd` hint narrows its account chooser as
well, but the check that counts is the one on the server.

### Admin

`/admin/` needs a staff user. Sign in on the site with Google once, then run
`deploy manage make_admin you@example.com` (table above). No password is involved: the
admin accepts the session you already have. From then on an **Admin** entry shows in
the site's navigation, and **Users** in the menu behind your initial.

Under Users you see everyone who has signed in, how much they have graded and when
they last studied. Opening a person shows their progress by course. You can:

- correct a name, or switch **Active** off to lock someone out without deleting anything;
- tick **Staff** and **Superuser** to make someone an administrator;
- delete a person, which removes their progress with them;
- select people in the list and choose **Clear the study progress**: the accounts stay,
  and their devices drop their own copies at the next visit.

Course content stays read-only there; it changes by publishing files.

`deploy manage` needs the server's copy of the deploy script from October 2026 or later
to work over plain `ssh` (older copies want `ssh -t`). The script is one of the
[files a deploy cannot change](#what-a-deploy-cannot-change): install it again with
`deploy install` after pulling.

### An open site

`DSAI_REQUIRE_LOGIN=0` in the server's `.env` removes the requirement: no landing page,
everything public again, and progress stays in each browser as it did before accounts.

```
printf 0 | ssh <server> dsai-vault/bin/deploy setenv DSAI_REQUIRE_LOGIN
ssh <server> dsai-vault/bin/deploy restart
```

## If you later get a real domain

Add it to Cloudflare and attach it to the Pages project as a custom domain: the proxy
keeps working unchanged. Then set `PUBLIC_HOST` in the server's `.env` to the new name and
run `deploy restart`. With a domain, a Cloudflare Tunnel could also replace the Pages
proxy and the open port entirely; that is a tidier design, not a necessary one.
