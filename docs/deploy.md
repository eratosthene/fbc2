# Deploying fbc from the image CI builds

How the live server runs the image GitHub Actions built, instead of building
its own. For the rest of running the app live, see
[Running fbc in production](production.md).

This is in two steps. **Step 1** is built: CI publishes the image, and a script
on the server runs a tag you pick, checks it came up, and goes back if it
didn't. **Step 2**, deploying by itself, is a design only; it's at the end.

## Why

Before this, a deploy was `git pull` and `docker/live-up.sh` on the NAS, which
builds the image there. That builds on a slow machine, can differ from what CI
tested, and leaves no earlier image to go back to. Now CI builds the image
once, checks it, and the server runs that exact image.

## How it works

On every push to `main`, the `publish` job in `.github/workflows/docker.yml`:

1. waits for the build and its checks to pass;
2. builds the image for `linux/amd64`, the NAS's CPU, and pushes it as
   `ghcr.io/eratosthene/fbc2:sha-<first 7 characters of the commit>`;
3. pulls that pushed image and runs the same checks on it (packages import,
   no `config.py` inside);
4. only then moves the tag `main` to it.

On pull requests the job is skipped, so nothing is published from a branch.

Not every merge builds an image. The Docker workflow only runs when a change
touches what goes into the image: `Pipfile`, `Pipfile.lock`, `fbc/`,
`migrations/`, the Dockerfile, its `.dockerignore` and entrypoint, and the
workflow and smoke check themselves. A merge that only changes docs, tests,
scripts, the compose files or the other workflows publishes nothing, so that
commit has no `sha-` tag. To deploy, use the newest tag that exists: open the
repository's Packages and open `fbc2`, or run
`git log -1 --format=%h -- Pipfile Pipfile.lock fbc migrations docker/Dockerfile docker/docker-entrypoint.sh`
and use `sha-` plus that. The `main` tag always points at the latest image.
If the Dockerfile ever copies something new, add its path to the workflow's
`paths` list.

Two kinds of tag, with different jobs:

| Tag           | Moves?      | Use                                                              |
| ------------- | ----------- | ---------------------------------------------------------------- |
| `sha-1a2b3c4` | Never       | What the server runs. Deploys and rollbacks name one.            |
| `main`        | Every merge | "The latest main". Handy to look at; don't pin the server to it. |

The server pins one `sha-` tag as `FBC_IMAGE` in `docker/.env`.
`docker/compose.yml` runs `${FBC_IMAGE}`, or `fbc2:latest` (what
`docker/build.sh` builds) when it isn't set, so nothing changes until the
first deploy.

The image has no settings or secrets in it: `config.py` and `docker/.env`
stay on the server, mounted and read in as before. That is also why the
image is safe to publish.

### Deploying and rolling back

On the NAS, from the repo checkout (`git pull` there still updates the
scripts):

```bash
docker/live-deploy.sh                  # show what runs and what ran before
docker/live-deploy.sh sha-1a2b3c4      # run that commit's image
docker/live-deploy.sh --previous       # go back to the one before
```

A deploy:

1. pulls the image (if that fails, nothing has changed);
2. compares the migrations in it with those in the running image;
3. records the running image in `docker/.previous-image`, and sets
   `FBC_IMAGE` in `docker/.env` (the rest of that file is left alone);
4. recreates the container, which runs `setup-db` as it starts;
5. waits up to 5 minutes (`--timeout`) for the container's health check, which
   passes once `setup-db` is done and the login page answers;
6. goes back to the previous image if that fails (see below), and records each
   result in `docker/deploys.log`.

### An emergency: building on the server

If CI's image isn't an option (GitHub is down, or you need a fix that isn't
merged), build this checkout's code on the NAS and run it with the same
checks and rollback:

```bash
docker/live-deploy.sh --local
```

It runs `docker/build.sh`, keeps the image that was running as
`fbc2:previous` so it can go back to it, and sets `FBC_IMAGE=fbc2:latest`.
It asks for `--backed-up` when the migrations differ, like any deploy. When
CI's image is an option again, deploy a tag as usual. Don't edit
`FBC_IMAGE` out of `docker/.env` by hand for this: `docker/live-deploy.sh`
then loses track of what to go back to.

A rollback by hand is one line: change `FBC_IMAGE` in `docker/.env` to an
older tag, then `docker/live-up.sh -n`. `docker/live-deploy.sh --previous` does
that and waits for health.

`docker/live-up.sh` still builds and runs a local image when `FBC_IMAGE` isn't
set. When it is set, it won't build, and `-n` just recreates the container
(for a changed `config.py` or `docker/.env`). The other live scripts
(`live-down.sh`, `live-logs.sh`, `live-flask.sh`) are unchanged: compose reads
`docker/.env` itself.

### Migrations and rolling back

Migrations only go forward. A newer image's `setup-db` changes the database
as it starts, and an older image can't undo that. Worse, an older image
started on a database that is ahead of it stops at `setup-db`, because Alembic
doesn't know the database's revision.

| Deploy changes                                                          | Going back to the old image                                                                                                 |
| ----------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| No migration                                                            | Safe. This is all the automatic rollback does.                                                                              |
| A migration that only adds (a column, a table, an index; #108 did this) | The old image still can't start, as above, but the data is intact. Restore the backup, or deploy the fixed version instead. |
| A migration that drops or changes (#94 dropped columns)                 | The data in what was dropped is gone. Only a restore from a backup undoes it.                                               |

So the script treats any difference in migrations as a reason to stop and make
you back up first, and it never rolls back by itself after one: the new image
may have changed the database before failing.

When a deploy brings a new migration:

1. Back up the database, from the NAS, with MariaDB's own tools. Name the
   backup after the tag you're about to deploy:

   ```bash
   mysqldump --single-transaction -h <host> -u <user> -p fbc \
     | gzip > fbc-before-sha-2222222.sql.gz
   ```

2. Deploy, saying you did:

   ```bash
   docker/live-deploy.sh sha-2222222 --backed-up
   ```

3. If it doesn't come up, fix forward with a newer tag if the cause is small.
   To undo it, stop the app (`docker/live-down.sh`), restore the backup into
   an emptied database, then `docker/live-deploy.sh --previous --backed-up`.
   Anything entered since the backup is lost, so decide quickly.

The first deploy compares against the locally built `fbc2:latest`, and asks
for `--backed-up` if that can't be read. Take a backup before it anyway.

## What to do by hand to turn it on

In this order. Nothing here is done by the PR.

1. Merge the PR. CI publishes the first image on that merge. In GitHub, open
   the repository's Packages and open `fbc2`; this shows the tag
   `sha-<commit>`.

2. Choose how the NAS pulls it (see [Which registry](#which-registry)). The
   package may start out private.

   - Public: in the package's settings, set its visibility to public. The
     NAS needs nothing.
   - Private: make a token, as below, and log in on the NAS once.

3. On the NAS, check what Container Manager has, rather than assuming:

   ```bash
   sudo docker version
   sudo docker compose version
   uname -m
   ```

   The compose file is written for Compose 2.20, and the image is
   `linux/amd64`, so `uname -m` must say `x86_64`.

4. On the NAS, `git pull` in the repo checkout to get the new scripts.

5. Back up the database, since the first deploy may find different migrations
   to the locally built image.

6. Deploy the commit CI published:

   ```bash
   docker/live-deploy.sh sha-1a2b3c4 --backed-up
   ```

7. Watch it, and check the site through Synology's reverse proxy:

   ```bash
   docker/live-logs.sh
   ```

8. Write the tag you deployed down somewhere you'll find it; after this,
   `docker/live-deploy.sh` with no arguments shows it, and
   `docker/deploys.log` keeps the history.

### Private package: the token

GitHub's fine-grained tokens can't read packages yet, so this needs a classic
personal access token with only the `read:packages` scope. Anyone who gets it
can read your packages, and nothing else.

1. GitHub > Settings > Developer settings > Personal access tokens > Tokens
   (classic) > Generate new token, scope `read:packages`, no expiry or a long
   one that you'll note to renew.

2. On the NAS:

   ```bash
   echo '<token>' | sudo docker login ghcr.io -u eratosthene --password-stdin
   ```

   Docker keeps it in root's `~/.docker/config.json`, base64 encoded, not
   encrypted. Keep that file mode 600.

## Which registry

| Way                                                       | Setup                                                                                                       | Secrets on the NAS                                      | Bandwidth                                                         | Verdict                                            |
| --------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- | ------------------------------------------------------- | ----------------------------------------------------------------- | -------------------------------------------------- |
| **ghcr.io, public**                                       | None. CI's own token pushes it.                                                                             | None                                                    | The NAS downloads only layers it lacks; the usual change is small | **Recommended.**                                   |
| ghcr.io, private                                          | A classic token and one `docker login`                                                                      | A read-only token                                       | Same                                                              | Use if you don't want it public.                   |
| Artifact, NAS downloads it                                | CI uploads a `docker save`; the NAS needs a token that can read Actions artifacts, and a script to fetch it | A token, with broader scope than `read:packages`        | The whole image every time; artifacts expire                      | Worse than a private package in every way.         |
| CI pushes over SSH (`docker save \| ssh nas docker load`) | Needs the NAS reachable from the internet, or a tunnel, plus an SSH key for CI                              | An SSH key in GitHub, which can run commands on the NAS | The whole image every time, over your uplink                      | Doesn't fit: the NAS takes no inbound connections. |

Public costs nothing real here. The repository is already public, the image
holds the same code, and the CI check fails the build if `config.py` is in
it. The real secrets live only in `config.py` and `docker/.env` on the NAS.
Private is for preference, not safety.

The NAS only ever has to make outbound connections, which an ordinary home
connection allows. Layers that didn't change, such as the installed
packages, aren't downloaded again, so most deploys are a few megabytes. A
`docker save | docker load` always moves the whole image.

## How the NAS pulls

| Way                                                       | Fits the NAS?                                                                                                                                                                                                                                                        |
| --------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **You run `docker/live-deploy.sh`** (step 1)              | Yes. Outbound only. You choose when.                                                                                                                                                                                                                                 |
| A scheduled script on the NAS polls the registry (step 2) | Yes. Outbound only, and nothing new to install.                                                                                                                                                                                                                      |
| Watchtower                                                | Not recommended. It replaces the container as soon as the tag moves, which doesn't back anything up, doesn't know about migrations, and doesn't go back if the app fails. Upstream's repository was also archived, as far as I know; check before relying on a fork. |
| CI pushes over SSH                                        | No: needs inbound access to the NAS, or a tunnel and a key that can run commands on it.                                                                                                                                                                              |
| A self-hosted GitHub runner on the NAS                    | Works, outbound only, but an always-on runner on an Apollo Lake NAS is a lot to run for this, and a pull request that can reach it can run code on your server.                                                                                                      |

So the NAS pulls. The commands above only need DSM's Container Manager (the
Docker and Compose it ships) and `sudo`, as the scripts already do. Features
of newer Docker or Compose, such as the `start_interval` of a health check or
`docker compose up --wait`, aren't used, because Container Manager may not
have them.

## Step 2: deploying by itself

Not built. This is the design, to build once step 1 has worked for a while.
It adds, in this order of importance, the guardrails you asked for:

- **Only from `main`, after CI.** The `publish` job already only runs on
  `main`, after the build. The deploy would be a separate workflow, started by
  the Tests and Docker build workflows finishing on `main`, which checks both
  passed for that commit. The migrations check runs on the pull request that
  adds the migration; with branch protection requiring it, it has passed
  before the merge.
- **A manual approval.** The deploy workflow's job uses a GitHub
  environment, `production`, with you as its required reviewer. It then moves
  a second moving tag, `prod`, to the approved `sha-` tag. Nothing is
  deployed until you click. Turning the approval off is removing the
  reviewer.
- **The NAS follows `prod`.** A Task Scheduler job (Control Panel > Task
  Scheduler > a user-defined script, run as root, every 5 minutes) runs a new
  `docker/live-deploy.sh --if-newer prod`. It pulls `prod`, and does
  nothing if the digest is the one already running. This is outbound only.
- **Migrations are never automatic.** If `prod`'s migrations differ from
  the running image's, the script doesn't deploy; it records that a
  migration is waiting, and you run `docker/live-deploy.sh --backed-up`
  after taking a backup. An automatic deploy only ever applies the case
  where going back is safe.
- **A health check, and automatic rollback.** Already in step 1: the script
  waits for health, and goes back to the previous image if there were no new
  migrations.
- **Rollback from GitHub.** A manual workflow that moves `prod` back to a
  given `sha-` tag, for when the NAS is behind or you are away from it. The
  NAS would follow the change within 5 minutes.
- **Knowing it happened.** DSM can email from a Task Scheduler job's result.
  Its notifications would be the way to hear about a rollback, since no one is
  watching the logs.

Reasons to wait: the approval click and the polling each need something on
GitHub's side (an environment, a second tag) and on the NAS (a scheduled
task) that can only be tried live, and the manual script is what they would
call, so it should run first.

## What wasn't tested

The workflow job, the registry login, and anything on the NAS: they can only
run on GitHub and on the server. The script is tested against a stand-in for
Docker (`tests/test_live_deploy.py`): the steps it takes, and what it does when
the image won't pull, the migrations differ, or the app isn't healthy. The
compose file's `${FBC_IMAGE}` and health check were rendered with
`docker compose config`, but the health check hasn't run in a real container
against a database.
