# Company CA certificates for the API image

Your company CA's certificates (`*.crt` or `*.pem`, PEM), built into the API image (#795): `docker compose up -d --build` passes this folder to the build (`api/Dockerfile`, the `certs` context), which adds them to the image's trusted CAs, next to the public ones. Nothing here except this README is ever committed (`.gitignore`). With no files, the image is the same as without the folder.

They're trusted by:

- **the build itself:** the company's package index for [`packages/`](../packages/README.md) (without the CA, uv stops with `invalid peer certificate: UnknownIssuer`);
- **the API at run time**, for anything that uses the system's trusted CAs: Nautobot, LibreNMS, the wireless controllers, e-mail, and the AD package's LDAPS when it uses them (check with whoever maintains it). `EXTRA_CA_PEM` in `.env` does the same for the app's own HTTPS without rebuilding.

## Which file

The CA that signed your servers' certificates; an issuing (intermediate) CA is enough. A host that already trusts it usually has it here:

```bash
ls /usr/local/share/ca-certificates/ /etc/pki/ca-trust/source/anchors/ 2>/dev/null
cp /usr/local/share/ca-certificates/<your-ca>.crt certs/
docker compose up -d --build
```

Not on the host? Which CA signed a server's certificate:

```bash
echo | openssl s_client -connect <host>:443 -showcerts 2>/dev/null | grep -E 's:|i:'
```
