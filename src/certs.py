"""Keep a usable TLS CA bundle reachable for the whole life of the process.

py2app zips pure-Python packages into python3xx.zip, and certifi is not in
setup.py's "packages" list by default, so cacert.pem ends up inside that zip
rather than on disk. certifi.where() copes by extracting it to a temp file
under $TMPDIR and caching the path in a module global; requests snapshots the
same value exactly once, at import time, into DEFAULT_CA_BUNDLE_PATH.

macOS's dirhelper purges $TMPDIR of anything untouched for ~3 days. SpeakrFlow
is a menu bar app that stays running for weeks, so after a few quiet days the
cached path names a file that no longer exists, and every transcription dies on

    Could not find a suitable TLS CA certificate bundle, invalid path: /var/...

which reads like a broken install and sends you auditing keychains. Shipping
certifi unzipped (see setup.py) stops the extraction happening at all. This
module is the belt to that pair of braces: it re-checks the path before each
request and, if it has gone, rewrites the bundle somewhere macOS won't sweep.
"""
import os

import certifi

from .paths import DATA_DIR

# Application Support, not $TMPDIR — nothing here is subject to the purge.
_BUNDLE = DATA_DIR / "cacert.pem"


def _materialize():
    """Write our own copy of certifi's bundle and return its path.

    certifi.contents() reads straight out of the zip, so it still works when
    the extracted temp file is the very thing that went missing.
    """
    _BUNDLE.parent.mkdir(parents=True, exist_ok=True)
    # Write-then-rename so a crash mid-write can't leave a truncated bundle,
    # which would fail as a hard TLS error rather than a missing-file one.
    staged = _BUNDLE.with_name(_BUNDLE.name + ".tmp")
    staged.write_text(certifi.contents(), encoding="ascii")
    staged.replace(_BUNDLE)
    return str(_BUNDLE)


def ca_bundle():
    """Return a CA bundle path that exists right now."""
    path = certifi.where()
    if os.path.exists(path):
        return path
    return _materialize()
