# The seat: Vademecum's away node on Cloudflare

One container running the same Vademecum the Mac runs, fronted by a Worker, with its records
snapshotted to an R2 bucket so they survive the container being replaced
([ADR 0017](../../docs/adr/0017-the-seat-on-cloudflare.md)). The Mac stays **home** and syncs
with it ([ADR 0015](../../docs/adr/0015-two-vademecums-that-sync.md)); the phone reaches the
dashboard and the assistants' tools through it.

What is here:

- `Dockerfile` — builds the web app, installs the API and the MCP server, adds `rclone`.
- `seat.py` — the supervisor: restore from R2, start the API and the gateway, snapshot every
  five minutes and at shutdown. `tests/test_seat.py` covers it.
- `worker/` — the Worker and its `wrangler.jsonc`: a Durable Object that holds the one
  container and forwards every request to the gateway's port.

## Deploy, once

You need a Cloudflare account on the Workers paid plan (containers need it), `node` and
`npm` on the Mac, and nothing else. Everything below runs from this directory.

1. **A bucket for the snapshots.** In the Cloudflare dashboard, R2 → Create bucket, named
   `vademecum`. Then R2 → Manage API tokens → create a token with object read and write on
   that bucket; keep its access key id and secret. Note the S3 endpoint it shows,
   `https://<account id>.r2.cloudflarestorage.com`.
2. **Fill in the Worker's variables.** In `worker/wrangler.jsonc` set `R2_ENDPOINT` to that
   endpoint. Leave `PUBLIC_URL` for a moment.
3. **Secrets.** From `worker/`:

   ```sh
   npm install
   npx wrangler login
   npx wrangler secret put R2_ACCESS_KEY_ID
   npx wrangler secret put R2_SECRET_ACCESS_KEY
   npx wrangler secret put VADEMECUM_SYNC_ACCEPT_TOKEN     # a long random string; the Mac will present it
   npx wrangler secret put VADEMECUM_MCP_PASSPHRASE        # at least 12 characters; you sign in with it
   ```

4. **Deploy.** `npx wrangler deploy`. The output names the Worker's address,
   `https://vademecum-seat.<you>.workers.dev`. Put that into `PUBLIC_URL` in `wrangler.jsonc`
   and deploy once more, so the gateway knows its own origin.
5. **Check.** Open the address in a browser: the Vademecum sign-in page, asking for the
   passphrase. `https://<address>/health` answers `{"status": "ok"}`.

## Connect the Mac

On the Mac, record the seat as the peer (the settings file both processes read):

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

- Between snapshots the seat can lose up to five minutes of phone-side work if the container
  is replaced. The Mac is never behind by more than its last sync.
- The seat has no macOS frameworks: a file taken in on the phone is text-only until the Mac
  has synced and read it properly.
- After updating Vademecum, `npx wrangler deploy` again; the seat restores its records at boot.
- This repository cannot test a Cloudflare deployment. The container pieces are tested in
  isolation; the Worker's container API should be checked against Cloudflare's current
  documentation for `@cloudflare/containers` at deploy time.
