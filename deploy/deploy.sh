#!/usr/bin/env bash
# The server side of every deploy. On the server this file is ~/dsai-vault/bin/deploy.
#
#   deploy install <folder> [host]   put compose.yaml, Caddyfile and this script in place;
#                                    on first use also create .env with new secrets
#   deploy app <commit>              reads a .tar.gz of the code on standard input:
#                                    build the image, migrate, restart, re-sync content
#   deploy content                   reads a .tar.gz of course files on standard input:
#                                    validate them, then replace the live ones and load them
#   deploy status | logs | restart   look at, or restart, what is running
#   deploy manage <args>             run manage.py inside the running container
#   deploy ci                        the only thing the GitHub Actions key may run
#
# Nothing here needs root; the user must be allowed to run docker. docs/DEPLOYMENT.md
# explains the whole arrangement.

set -euo pipefail
umask 022

ROOT="${DSAI_ROOT:-$HOME/dsai-vault}"
KEEP_RELEASES=3

say() { printf '\n==> %s\n' "$*"; }
die() {
	printf 'deploy: %s\n' "$*" >&2
	exit 1
}

compose() { docker compose --project-directory "$ROOT" --file "$ROOT/compose.yaml" "$@"; }

# Two deploys at once would trip over each other; the second one stops here.
lock() {
	exec 9>"$ROOT/.deploy.lock"
	flock --nonblock 9 || die "another deploy is running, try again in a minute"
}

random_secret() { openssl rand -hex 32; }

cmd_install() {
	local source="${1:-}" host="${2:-}"
	[[ -f "$source/compose.yaml" && -f "$source/Caddyfile" && -f "$source/deploy.sh" ]] ||
		die "usage: deploy install <folder with compose.yaml, Caddyfile, deploy.sh> [public-host]"

	mkdir -p "$ROOT/bin" "$ROOT/courses" "$ROOT/releases"
	install -m 644 "$source/compose.yaml" "$ROOT/compose.yaml"
	install -m 644 "$source/Caddyfile" "$ROOT/Caddyfile"
	install -m 755 "$source/deploy.sh" "$ROOT/bin/deploy"
	say "installed compose.yaml, Caddyfile and bin/deploy in $ROOT"

	if [[ ! -f "$ROOT/.env" ]]; then
		[[ -n "$host" ]] || die "first install: also give the public host name, e.g. dsai3.pages.dev"
		# The secrets are generated here and written straight to the file: they never
		# appear on screen or in a log.
		(
			umask 077
			{
				printf 'PUBLIC_HOST=%s\n' "$host"
				printf 'DJANGO_SECRET_KEY=%s\n' "$(random_secret)"
				printf 'ORIGIN_SECRET=%s\n' "$(random_secret)"
			} >"$ROOT/.env"
		)
		say "wrote $ROOT/.env with new secrets (readable by you only)"
	else
		say "kept the existing $ROOT/.env"
	fi

	# The proxy joins the Docker network of whatever already serves port 443 here.
	if ! grep -q '^FRONT_NETWORK=' "$ROOT/.env"; then
		local front network
		front="$(docker ps --filter publish=443 --format '{{.Names}}' | head -n 1)"
		[[ -n "$front" ]] ||
			die "no container publishes port 443; add FRONT_NETWORK=<docker network> to $ROOT/.env"
		network="$(docker inspect "$front" \
			--format '{{range $k, $v := .NetworkSettings.Networks}}{{println $k}}{{end}}' | head -n 1)"
		printf 'FRONT_NETWORK=%s\n' "$network" >>"$ROOT/.env"
		say "the proxy will join '$network', the Docker network of '$front'"
	fi

	if docker image inspect dsai-vault:live >/dev/null 2>&1; then
		say "run 'deploy restart' for the new configuration to take effect"
	fi
}

wait_until_up() {
	local attempt
	for attempt in $(seq 1 30); do
		if compose exec -T web python deploy/smoke.py >/dev/null 2>&1; then
			return 0
		fi
		sleep 2
	done
	compose logs --tail 40 web >&2 || true
	die "the site did not come up within a minute; the log lines above say why"
}

check_proxy() {
	# From inside the proxy container, so the secret is read there and never printed.
	# shellcheck disable=SC2016
	compose exec -T proxy sh -c \
		'wget -q -O /dev/null --header "X-Origin-Auth: $ORIGIN_SECRET" http://127.0.0.1:8080/' ||
		die "the proxy does not answer a request that carries the secret"
	if compose exec -T proxy wget -q -O /dev/null http://127.0.0.1:8080/ 2>/dev/null; then
		die "the proxy answers WITHOUT the secret: do not put this online"
	fi
	echo "  proxy: answers with the secret, refuses without it"
}

prune() {
	local old
	# Newest first; everything after the first few goes, with its image.
	while IFS= read -r old; do
		[[ -n "$old" ]] || continue
		rm -rf "${ROOT:?}/releases/$old"
		docker image rm "dsai-vault:$old" >/dev/null 2>&1 || true
	done < <(ls -1t "$ROOT/releases" | tail -n "+$((KEEP_RELEASES + 1))")
	docker image prune --force --filter "label=dsai.commit" >/dev/null 2>&1 || true
}

cmd_app() {
	local commit="${1:-}"
	[[ "$commit" =~ ^[0-9a-f]{7,40}$ ]] || die "usage: deploy app <commit> < release.tar.gz"
	[[ -f "$ROOT/compose.yaml" && -f "$ROOT/.env" ]] || die "run 'deploy install' first"
	lock

	local release="$ROOT/releases/$commit"
	rm -rf "$release"
	mkdir -p "$release"
	say "unpacking release $commit"
	tar -xzf - -C "$release" --no-same-owner --no-same-permissions
	[[ -f "$release/Dockerfile" ]] || die "the archive has no Dockerfile: is it this project?"

	say "building the image"
	docker build --tag "dsai-vault:$commit" --label "dsai.commit=$commit" "$release"
	docker tag "dsai-vault:$commit" dsai-vault:live

	say "applying database migrations"
	compose run --rm --no-deps -T web python manage.py migrate --noinput

	say "starting the new version"
	compose up --detach --remove-orphans
	wait_until_up

	# New code may render content differently, so load the course files again. A course
	# the new code rejects keeps its old rows; the failure is reported at the end.
	say "loading course content"
	local content_ok=1
	compose exec -T web python manage.py sync_content || content_ok=0

	say "checking the result"
	compose exec -T web python deploy/smoke.py
	check_proxy
	prune

	[[ "$content_ok" == 1 ]] || die "version $commit is live, but some course content did not load (see above)"
	say "version $commit is live"
}

cmd_content() {
	docker image inspect dsai-vault:live >/dev/null 2>&1 || die "deploy the app before any content"
	lock

	local incoming="$ROOT/incoming"
	rm -rf "$incoming"
	mkdir -p "$incoming"
	say "unpacking course files"
	tar -xzf - -C "$incoming" --no-same-owner --no-same-permissions
	[[ -n "$(find "$incoming" -mindepth 2 -maxdepth 2 -name course.yaml -print -quit)" ]] ||
		die "the archive contains no course (expected <course>/course.yaml)"

	# The same check authors run locally, against the new files, before anything live
	# changes. If it fails the script stops here and the site is untouched.
	say "validating"
	compose run --rm --no-deps -T \
		--volume "$incoming:/incoming:ro" --env DSAI_COURSES_DIR=/incoming \
		web python manage.py sync_content --check

	say "replacing the live course files"
	rsync --archive --delete "$incoming/" "$ROOT/courses/"
	rm -rf "$incoming"

	say "loading into the database"
	compose exec -T web python manage.py sync_content
	say "content is live"
}

cmd_status() {
	compose ps
	printf '\nversion: %s\n' "$(docker image inspect dsai-vault:live --format '{{ index .Config.Labels "dsai.commit" }}' 2>/dev/null || echo 'nothing deployed')"
	printf 'courses: %s\n' "$(find "$ROOT/courses" -mindepth 2 -maxdepth 2 -name course.yaml 2>/dev/null | wc -l)"
}

cmd_ci() {
	# In ~/.ssh/authorized_keys the GitHub Actions key is pinned to "deploy ci", so
	# whatever that key asks for, this function runs instead. The request itself arrives
	# in SSH_ORIGINAL_COMMAND, and exactly one shape of it is accepted.
	local request="${SSH_ORIGINAL_COMMAND:-}"
	[[ "$request" =~ ^app\ ([0-9a-f]{40})$ ]] || die "this key may only run: app <full commit id>"
	cmd_app "${BASH_REMATCH[1]}"
}

command="${1:-}"
shift || true
case "$command" in
install) cmd_install "$@" ;;
app) cmd_app "$@" ;;
content) cmd_content ;;
status) cmd_status ;;
logs) compose logs --tail "${1:-100}" web proxy ;;
restart) compose up --detach --force-recreate ;;
manage) compose exec web python manage.py "$@" ;;
ci) cmd_ci ;;
*) die "unknown command '${command}'. See the top of $0 for the list." ;;
esac
