# Foris: Vademecum's always-awake copy on Cloudflare

(First called "Foris"; the deploy folder, the Worker's address and the secret names keep that word. The Mac is "Domi".)

One container running the same Vademecum the Mac runs, fronted by a Worker, with its records
snapshotted to an R2 bucket so they survive the container being replaced
([ADR 0017](../../docs/adr/0017-the-seat-on-cloudflare.md)). The Mac stays the **Domi** and syncs
with it ([ADR 0015](../../docs/adr/0015-two-vademecums-that-sync.md)); the phone reaches the
dashboard and the assistants' tools through it.

What is here:

- `Dockerfile` — builds the web app, installs the API and the MCP server.
- `seat.py` — the supervisor: restore the databases from the snapshot store, start the API
  and the gateway, bring the stored files back in the background, snapshot every five minutes
  and at shutdown. `tests/test_seat.py` covers it.
- `worker/` — the Worker and its `wrangler.jsonc`: a Durable Object that holds the one
  container and forwards every request to the gateway's port, and the snapshot store under
  `/__seat/`, answered from the R2 bucket binding to a caller presenting `SEAT_KEY`.
- `tools/podman-as-docker` — lets wrangler build and push the image with podman.

## Deploy, once

You need a Cloudflare account on the Workers paid plan (containers need it), with R2 enabled
(both are one click each in the dashboard), `node` and `npm` on the Mac, and a container CLI
for the image build. Docker works as is. Without Docker: `brew install podman && podman machine
init && podman machine start`, then point wrangler at `tools/podman-as-docker`, a small shim
that makes podman answer the three things wrangler expects of docker (no `--provenance`, no
`manifest inspect -v`, and the pushed digest reported on the local image):

```sh
export WRANGLER_DOCKER_BIN="$PWD/../tools/podman-as-docker"
export DOCKER_HOST="unix://$(podman machine inspect --format '{{.ConnectionInfo.PodmanSocket.Path}}')"
```

With podman, run each `wrangler deploy` that changes the image **twice**: the first pass pushes
the image and records its digest, the second tells Cloudflare about it. Behind a workplace proxy
that re-signs TLS (Zscaler and the like), the podman VM also needs that proxy's root certificate:
export it from Keychain Access and `podman machine ssh "sudo tee
/etc/pki/ca-trust/source/anchors/proxy.pem && sudo update-ca-trust" < proxy.pem`.

Everything below runs from `worker/`.

1. **Sign in and make the bucket.** `npx wrangler login`, then
   `npx wrangler r2 bucket create vademecum`. No R2 API token is needed: the Worker reaches
   the bucket through a binding, and the container reaches it through the Worker.
2. **Deploy once** to create the Worker and build the image: `npm install && npx wrangler
   deploy`. The output names the address, `https://vademecum-seat.<you>.workers.dev`.
3. **Secrets.** Three, each a long random string except the passphrase, which you will type:

   ```sh
   npx wrangler secret put SEAT_KEY                        # the container's key to the snapshot store
   npx wrangler secret put VADEMECUM_SYNC_ACCEPT_TOKEN     # what the Mac presents to sync
   npx wrangler secret put VADEMECUM_MCP_PASSPHRASE        # at least 12 characters; you sign in with it
   ```

4. **Tell the Worker its own address.** Put the address from step 2 into `PUBLIC_URL` in
   `wrangler.jsonc` and `npx wrangler deploy` once more.
5. **Check.** Open the address in a browser: the Vademecum sign-in page, asking for the
   passphrase. `https://<address>/health` answers `{"status": "ok"}`.

## Connect the Mac

On the Mac, record Foris as the peer (the settings file both processes read). The Mac's
HTTPS client trusts the system keychain, so a workplace proxy is no obstacle here.

```sh
./scripts/mcp.sh setup sync https://vademecum-seat.<you>.workers.dev   # asks for the token
```

or set `VADEMECUM_SYNC_PEER_URL` and `VADEMECUM_SYNC_TOKEN` by hand. From then on the Mac
syncs every five minutes while Vademecum runs, and `python -m vademecum sync` runs one round
now. The first round carries the whole bank up.

## Connect the phone

- **ChatGPT:** Settings → Apps & Connectors → developer mode → add the MCP server
  `https://<address>/mcp`. Approve it with the passphrase. It is then available on the phone.
- **Claude:** Settings → Connectors → add custom connector with the same address.
- **The dashboard:** open the address in Safari, sign in, Add to Home Screen.

## Things to know

- Between snapshots Foris can lose up to five minutes of phone-side work if the container
  is replaced. The Mac is never behind by more than its last sync.
- A single stored file over about 95 MB is not snapshotted (the Worker in front will not
  carry more in one request); it stays on the Mac, which is Domi anyway.
- Foris has no macOS frameworks: a file taken in on the phone is text-only until the Mac
  has synced and read it properly.
- After updating Vademecum, `npx wrangler deploy` again; Foris restores its records at boot.
- This repository cannot test a Cloudflare deployment. The container pieces are tested in
  isolation; the Worker's container API should be checked against Cloudflare's current
  documentation for `@cloudflare/containers` at deploy time.
