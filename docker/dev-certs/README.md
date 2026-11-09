# Extra root CAs for the dev image

Put a root CA (a `.pem` or `.crt` file) here if a proxy on your network
re-signs HTTPS, such as Zscaler. `docker/compose.dev.yml` adds it to the dev
image's trust store and to certifi. The CA files are ignored by git, and the
live image never sees them. See "A proxy that re-signs HTTPS" in
`docs/development.md`.
