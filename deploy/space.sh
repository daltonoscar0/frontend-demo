#!/usr/bin/env bash
# Create or update the Hugging Face Space that serves stages 2 and 3.
#
#   hf auth login            # a write token, once
#   deploy/space.sh
#
# The Space is a Docker Space, so what gets pushed is `api/` and nothing else:
# the Dockerfile clones the three component repos at the pinned commits and
# bakes both sets of weights, the encoder and the corpora into the image. The
# build runs on Hugging Face's builder, not here, which is also the only place
# the image has been built -- there is no Docker on the machine this was written
# on, so the Space build log is the test.
#
# Idempotent: re-running updates the Space in place.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SPACE="${SPACE:-daltonoscar0/tts-front-end-api}"
STAGING="${STAGING:-$ROOT/.space}"

command -v git >/dev/null || { echo "git is required"; exit 1; }
HF="$ROOT/.venv/bin/hf"
[ -x "$HF" ] || HF="$(command -v hf)" || { echo "the hf CLI is required"; exit 1; }

if ! "$HF" auth whoami >/dev/null 2>&1; then
  echo "Not logged in to Hugging Face. Run:  hf auth login" >&2
  exit 1
fi
echo "as $("$HF" auth whoami | head -1)"

# The pins the image is built from, so the Space and the page's numbers agree.
echo
echo "component pins:"
make -C "$ROOT" pins

echo
if "$HF" repo info "$SPACE" --repo-type space >/dev/null 2>&1; then
  echo "updating existing Space $SPACE"
else
  echo "creating Space $SPACE"
  "$HF" repo create "$SPACE" --repo-type space --space_sdk docker -y
fi

# A clean checkout each time, so a file deleted here is deleted there.
rm -rf "$STAGING"
git clone -q "https://huggingface.co/spaces/$SPACE" "$STAGING"

# api/README.md carries the Space card (the YAML front matter HF reads), so it
# has to land as the Space's own README.
rm -f "$STAGING"/{Dockerfile,app.py,requirements.txt,README.md}
cp "$ROOT/api/Dockerfile" "$ROOT/api/app.py" "$ROOT/api/requirements.txt" "$STAGING/"
cp "$ROOT/api/README.md" "$STAGING/README.md"

cd "$STAGING"
git add -A
if git diff --cached --quiet; then
  echo "nothing changed; Space is already up to date"
  exit 0
fi
git -c user.name="Oscar Dalton" -c user.email="daltonoscar0@gmail.com" \
    commit -q -m "Stages 2 and 3 of the TTS front end

Built from https://github.com/daltonoscar0/frontend-demo at
$(git -C "$ROOT" rev-parse --short HEAD)."
git push -q origin main

echo
echo "pushed. The build takes a few minutes; watch it at"
echo "  https://huggingface.co/spaces/$SPACE?logs=build"
echo
echo "then check:"
echo "  curl -s https://${SPACE/\//-}.hf.space/health | python3 -m json.tool"
