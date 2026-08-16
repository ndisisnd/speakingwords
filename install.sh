#!/bin/sh
# speakingwords installer — the cURL path (plan §6, assertion A8).
#
#   curl -fsSL <install-url> | sh
#
# This exists for people who do not want a global npm install. It must land the
# SAME file tree npm lands: byte-identical skill/, bin/ and lib/. It does that
# the only way that can stay true — it installs the published release tarball,
# which is the npm tarball, rather than re-assembling the tree itself.
#
# What it does
#   1. Checks Node >= 18 (the CLI is Node; there is no Node-free mode).
#   2. Downloads the release tarball named by SPEAKINGWORDS_URL.
#   3. Verifies its SHA-256 against SPEAKINGWORDS_SHA256, and refuses to
#      install without one unless --insecure is passed explicitly.
#   4. Extracts to ~/.speakingwords/app and symlinks the CLI into ~/.local/bin.
#
# The default download URL is the published npm tarball for this version, with
# its SHA-256 baked in below. The maintainer refreshes both on every release.
# SPEAKINGWORDS_URL / SPEAKINGWORDS_SHA256 still override them for testing or for
# installing a different build.
#
# POSIX sh only. No bashisms, no arrays, no [[ ]], no local.

set -eu

VERSION_HINT="0.3.1"

# Set by the maintainer at release time — the published npm tarball for this
# version, and its SHA-256.
DEFAULT_URL="https://registry.npmjs.org/speakingwords/-/speakingwords-0.3.1.tgz"
DEFAULT_SHA256="0115bee93030561ce445cbc2b53b498a8bc8f97821e180bc4b0bb7b84b869f7a"

URL="${SPEAKINGWORDS_URL:-$DEFAULT_URL}"
SHA256="${SPEAKINGWORDS_SHA256:-$DEFAULT_SHA256}"
INSECURE=0
ACTION="install"

PREFIX="${SPEAKINGWORDS_PREFIX:-$HOME/.speakingwords}"
APP_DIR="$PREFIX/app"
BIN_DIR="${SPEAKINGWORDS_BIN_DIR:-$HOME/.local/bin}"
LINK="$BIN_DIR/speakingwords"

TMP_DIR=""

# ------------------------------------------------------------------- helpers

say() {
	printf '%s\n' "$*"
}

err() {
	printf '%s\n' "$*" >&2
}

die() {
	err ""
	err "speakingwords install failed: $*"
	err ""
	exit 1
}

cleanup() {
	if [ -n "$TMP_DIR" ] && [ -d "$TMP_DIR" ]; then
		rm -rf "$TMP_DIR"
	fi
}
trap cleanup EXIT HUP INT TERM

usage() {
	cat <<'USAGE'
speakingwords installer

Usage
  curl -fsSL <install-url> | sh
  sh install.sh [--url URL] [--sha256 SUM] [--insecure]
  sh install.sh --uninstall

Options
  --url URL          release tarball to install (or set SPEAKINGWORDS_URL)
  --sha256 SUM       expected SHA-256 of that tarball (or SPEAKINGWORDS_SHA256)
  --insecure         install without a checksum. Not recommended, and never
                     the default: an unverified tarball is arbitrary code.
  --uninstall        remove the symlink and the installed tree
  -h, --help         this text

Environment
  SPEAKINGWORDS_URL         release tarball URL, or a local path / file:// URL
  SPEAKINGWORDS_SHA256      expected SHA-256 checksum of that tarball
  SPEAKINGWORDS_PREFIX      install root          (default ~/.speakingwords)
  SPEAKINGWORDS_BIN_DIR     where the symlink goes (default ~/.local/bin)

Requires Node.js >= 18. The npm path is `npm i -g speakingwords`, and installs
exactly the same files.
USAGE
}

# ---------------------------------------------------------------- arg parsing

while [ $# -gt 0 ]; do
	case "$1" in
	--url)
		[ $# -ge 2 ] || die "--url needs a value."
		URL="$2"
		shift 2
		;;
	--url=*)
		URL="${1#--url=}"
		shift
		;;
	--sha256)
		[ $# -ge 2 ] || die "--sha256 needs a value."
		SHA256="$2"
		shift 2
		;;
	--sha256=*)
		SHA256="${1#--sha256=}"
		shift
		;;
	--insecure)
		INSECURE=1
		shift
		;;
	--uninstall)
		ACTION="uninstall"
		shift
		;;
	-h | --help)
		usage
		exit 0
		;;
	*)
		err "Unknown option: $1"
		err ""
		usage >&2
		exit 1
		;;
	esac
done

# ------------------------------------------------------------------ uninstall

if [ "$ACTION" = "uninstall" ]; then
	removed=0

	if [ -L "$LINK" ]; then
		rm -f "$LINK"
		say "  removed symlink  $LINK"
		removed=1
	elif [ -e "$LINK" ]; then
		err "  left alone       $LINK (not a symlink — speakingwords did not create it)"
	fi

	if [ -d "$APP_DIR" ]; then
		rm -rf "$APP_DIR"
		say "  removed tree     $APP_DIR"
		removed=1
	fi

	# Only clear the prefix when nothing else lives in it.
	if [ -d "$PREFIX" ] && [ -z "$(ls -A "$PREFIX" 2>/dev/null)" ]; then
		rmdir "$PREFIX"
		say "  removed dir      $PREFIX"
	fi

	if [ "$removed" -eq 0 ]; then
		say ""
		say "Nothing to uninstall — no speakingwords install found at $APP_DIR."
		say ""
		exit 0
	fi

	say ""
	say "speakingwords removed."
	say ""
	say "  Your installed style contract was NOT touched. The hook wiring, the memory"
	say "  block and hits.jsonl live in the agent's own config, not here. Run"
	say "  \`speakingwords unhook\` BEFORE uninstalling to take enforcement out cleanly;"
	say "  if you already removed the CLI, edit the settings file by hand."
	say ""
	exit 0
fi

# ------------------------------------------------------------ pre-flight: node

if ! command -v node >/dev/null 2>&1; then
	die "Node.js is not installed, or not on PATH.

  speakingwords $VERSION_HINT is a Node CLI and needs Node.js >= 18. There is no
  Node-free install path in this version. Install Node (https://nodejs.org),
  reopen your shell, and run this again."
fi

NODE_VERSION="$(node --version 2>/dev/null || true)"
NODE_MAJOR="$(printf '%s' "$NODE_VERSION" | sed -n 's/^v\([0-9][0-9]*\).*/\1/p')"

if [ -z "$NODE_MAJOR" ]; then
	die "Could not read a version from \`node --version\` (got: '$NODE_VERSION').

  speakingwords needs Node.js >= 18."
fi

if [ "$NODE_MAJOR" -lt 18 ]; then
	die "Node.js $NODE_VERSION is too old.

  speakingwords needs Node.js >= 18 — it uses the node: import prefix and
  modern fs APIs. Upgrade Node and run this again."
fi

# --------------------------------------------------------- pre-flight: inputs

if [ -z "$URL" ]; then
	die "No download URL.

  This build has no baked-in release URL, so it needs one:

    SPEAKINGWORDS_URL=<tarball-url> sh install.sh
    sh install.sh --url <tarball-url>

  The tarball is the published npm tarball (speakingwords-<version>.tgz).
  If you have npm, \`npm i -g speakingwords\` is simpler and installs the same
  files."
fi

if [ -z "$SHA256" ] && [ "$INSECURE" -ne 1 ]; then
	die "No checksum.

  Piping a download into a shell without verifying it means running whatever
  the network hands you. Pass the release's SHA-256:

    SPEAKINGWORDS_SHA256=<sum> sh install.sh
    sh install.sh --sha256 <sum>

  If you accept that risk deliberately, pass --insecure."
fi

# ---------------------------------------------------------------- checksumming

sha256_of() {
	if command -v shasum >/dev/null 2>&1; then
		shasum -a 256 "$1" | cut -d' ' -f1
	elif command -v sha256sum >/dev/null 2>&1; then
		sha256sum "$1" | cut -d' ' -f1
	elif command -v openssl >/dev/null 2>&1; then
		openssl dgst -sha256 "$1" | sed 's/.*= *//'
	else
		return 1
	fi
}

# ------------------------------------------------------------------- download

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/speakingwords-install.XXXXXX")" ||
	die "Could not create a temporary directory."
TARBALL="$TMP_DIR/speakingwords.tgz"

say ""
say "speakingwords installer"
say ""
say "  source     $URL"

case "$URL" in
file://*)
	SRC_PATH="${URL#file://}"
	[ -f "$SRC_PATH" ] || die "No such file: $SRC_PATH"
	cp "$SRC_PATH" "$TARBALL" || die "Could not copy $SRC_PATH"
	;;
/* | ./* | ../*)
	[ -f "$URL" ] || die "No such file: $URL"
	cp "$URL" "$TARBALL" || die "Could not copy $URL"
	;;
*)
	if command -v curl >/dev/null 2>&1; then
		curl -fsSL "$URL" -o "$TARBALL" || die "Download failed: $URL"
	elif command -v wget >/dev/null 2>&1; then
		wget -qO "$TARBALL" "$URL" || die "Download failed: $URL"
	else
		die "Neither curl nor wget is available, so the tarball cannot be fetched."
	fi
	;;
esac

[ -s "$TARBALL" ] || die "Downloaded file is empty: $URL"

# ---------------------------------------------------------------- verification

if [ -n "$SHA256" ]; then
	ACTUAL="$(sha256_of "$TARBALL" || true)"
	if [ -z "$ACTUAL" ]; then
		die "No SHA-256 tool found (shasum, sha256sum or openssl), so the download
  cannot be verified. Install one, or re-run with --insecure if you accept
  that risk."
	fi
	if [ "$ACTUAL" != "$SHA256" ]; then
		die "Checksum mismatch — nothing was installed.

    expected  $SHA256
    actual    $ACTUAL

  The download does not match the release you asked for. Do not retry with
  --insecure; get the correct checksum from the release page first."
	fi
	say "  checksum   verified (sha256 $ACTUAL)"
else
	say "  checksum   SKIPPED (--insecure) — this tarball was not verified"
fi

# ------------------------------------------------------------------- extract

EXTRACT_DIR="$TMP_DIR/extract"
mkdir -p "$EXTRACT_DIR"
tar -xzf "$TARBALL" -C "$EXTRACT_DIR" || die "Could not extract the tarball."

# npm tarballs wrap everything in package/; a git archive uses <name>-<version>/.
# Either way there is exactly one top-level directory, and that is the tree.
SRC_DIR=""
for candidate in "$EXTRACT_DIR"/*; do
	if [ -d "$candidate" ]; then
		if [ -n "$SRC_DIR" ]; then
			SRC_DIR=""
			break
		fi
		SRC_DIR="$candidate"
	fi
done

[ -n "$SRC_DIR" ] || die "Unexpected tarball layout — expected one top-level directory."

for required in bin lib skill package.json; do
	[ -e "$SRC_DIR/$required" ] ||
		die "Tarball is missing $required — this is not a speakingwords release."
done

[ -f "$SRC_DIR/bin/speakingwords.js" ] ||
	die "Tarball has no bin/speakingwords.js — this is not a speakingwords release."

# --------------------------------------------------------------------- install

# Replace the tree wholesale rather than merging, so a downgrade cannot leave a
# stale file behind. The staging swap keeps the window where nothing is
# installed as short as possible.
mkdir -p "$PREFIX"
STAGING="$PREFIX/.app.incoming.$$"
rm -rf "$STAGING"
cp -R "$SRC_DIR" "$STAGING" || die "Could not stage the install into $STAGING"

rm -rf "$APP_DIR"
mv "$STAGING" "$APP_DIR" || die "Could not move the staged tree into $APP_DIR"

chmod 755 "$APP_DIR/bin/speakingwords.js" 2>/dev/null || true
for script in "$APP_DIR"/skill/scripts/*.py; do
	[ -f "$script" ] && chmod 755 "$script" 2>/dev/null || true
done

INSTALLED_VERSION="$(node -p "require('$APP_DIR/package.json').version" 2>/dev/null || true)"
[ -n "$INSTALLED_VERSION" ] || INSTALLED_VERSION="$VERSION_HINT"

# ---------------------------------------------------------------------- link

mkdir -p "$BIN_DIR" || die "Could not create $BIN_DIR"

if [ -e "$LINK" ] && [ ! -L "$LINK" ]; then
	die "$LINK already exists and is not a symlink.

  Something else owns that name. Move it, or install elsewhere with
  SPEAKINGWORDS_BIN_DIR=<dir>."
fi

rm -f "$LINK"
ln -s "$APP_DIR/bin/speakingwords.js" "$LINK" || die "Could not link $LINK"

# ------------------------------------------------------------------- summary

say "  installed  $APP_DIR"
say "  linked     $LINK"
say ""
say "speakingwords $INSTALLED_VERSION installed. Nothing is enforced yet."
say ""

case ":${PATH}:" in
*":$BIN_DIR:"*)
	say "  Next:  speakingwords init"
	;;
*)
	say "  $BIN_DIR is not on your PATH, so \`speakingwords\` will not be found yet."
	say "  Add this to your shell profile (~/.zshrc, ~/.bashrc, or ~/.profile):"
	say ""
	say "      export PATH=\"$BIN_DIR:\$PATH\""
	say ""
	say "  Then reopen the shell and run:  speakingwords init"
	;;
esac

say ""
say "  init asks three questions — mode, agent + scope, voice — and prints exactly"
say "  what it wrote and where. To remove the CLI later: sh install.sh --uninstall"
say "  (run \`speakingwords unhook\` first to take enforcement out)."
say ""
