#!/bin/zsh
# Build SpeakrFlow.app, sign it with a stable identity, and install it to
# /Applications.
#
# Signing with a real (non-adhoc) identity matters: macOS ties permission
# grants (Accessibility, Input Monitoring, Microphone) to the app's code
# signature. Ad-hoc signatures change on every build, which silently revokes
# Accessibility and breaks auto-paste after each rebuild. A stable identity
# keeps the grants across rebuilds.
set -euo pipefail
cd "$(dirname "$0")/.."

SIGN_IDENTITY="${SPEAKRFLOW_SIGN_IDENTITY:-Apple Development: henryzhao88@icloud.com (QQD752S82S)}"

echo "==> Building with py2app"
.venv/bin/python setup.py py2app

if security find-identity -v -p codesigning | grep -qF "$SIGN_IDENTITY"; then
    echo "==> Signing with: $SIGN_IDENTITY"
    codesign --force --deep --sign "$SIGN_IDENTITY" dist/SpeakrFlow.app
else
    echo "!! Signing identity not found: $SIGN_IDENTITY"
    echo "!! Leaving ad-hoc signature — permissions will reset on every rebuild."
fi

echo "==> Installing to /Applications"
pkill -f "SpeakrFlow.app/Contents/MacOS" 2>/dev/null || true
sleep 1
rm -rf /Applications/SpeakrFlow.app
ditto dist/SpeakrFlow.app /Applications/SpeakrFlow.app

echo "==> Launching"
open /Applications/SpeakrFlow.app
echo "Done. If this build's signature changed (first signed build), re-grant"
echo "permissions in System Settings → Privacy & Security."
