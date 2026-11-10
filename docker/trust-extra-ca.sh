#!/bin/sh
# Makes the image trust any extra root CAs in /extra-ca (*.pem or *.crt).
# With none there, it does nothing, which is what every build but the dev
# one wants. See "A proxy that re-signs HTTPS" in docs/development.md.
#
# It runs more than once while the image builds, because pip and apk need
# the CA before Python's packages are installed, and certifi (which requests
# uses) only exists after. Each step is safe to repeat.
set -eu

found=0
for cert in /extra-ca/*.pem /extra-ca/*.crt; do
    # A symlink to a file outside the folder arrives broken: BuildKit doesn't
    # follow it out of the build context. Say so, rather than build an image
    # that quietly lacks the CA.
    if [ -L "$cert" ] && [ ! -f "$cert" ]; then
        echo "ERROR: $(basename "$cert") in docker/dev-certs is a symlink the build can't follow. Copy the file there instead." >&2
        exit 1
    fi
    [ -f "$cert" ] || continue
    found=1
    name="$(basename "${cert%.*}")"
    cp "$cert" "/usr/local/share/ca-certificates/${name}.crt"
done
[ "$found" = 1 ] || exit 0

# The system store, which apk and curl use.
update-ca-certificates

# pip ships its own certifi, so point it at the system store.
printf '[global]\ncert = /etc/ssl/certs/ca-certificates.crt\n' > /etc/pip.conf

# certifi, once it is installed: add the CAs to its bundle, once.
if bundle="$(python -c 'import certifi; print(certifi.where())' 2>/dev/null)"; then
    for cert in /extra-ca/*.pem /extra-ca/*.crt; do
        [ -f "$cert" ] || continue
        if ! grep -qF "$(grep -v -e '-----' "$cert" | head -n 1)" "$bundle"; then
            printf '\n' >> "$bundle"
            cat "$cert" >> "$bundle"
        fi
    done
fi
