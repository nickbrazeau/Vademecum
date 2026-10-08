# 4. On your phone, and in ChatGPT or Claude

The Mac does the reading and building. The phone needs a copy that stays awake while the Mac
sleeps.

## The always-awake copy (optional)

`deploy/cloudflare` is the same Vademecum in one Cloudflare container. Its records are saved to
storage every five minutes. It syncs with the Mac: what is made from your files flows up, and
what you do on the phone flows back. It has no model and no key of its own.

Deploying it needs your own Cloudflare account. The whole procedure is in
[deploy/cloudflare/README.md](../../deploy/cloudflare/README.md). Once it is running, connect the
Mac:

```sh
./scripts/mcp.sh setup sync https://vademecum-seat.<you>.workers.dev
```

## The web app on the phone

Open the copy's address in Safari, sign in with your passphrase, then choose **Share → Add to
Home Screen**. When a newer version has been deployed, the app reloads itself the next time you
come back to it, as long as you have nothing typed.

## ChatGPT and Claude, including voice

Add the copy's MCP address, `https://<address>/mcp`, as a connector:

- **ChatGPT:** Settings → Apps & Connectors → developer mode → add the MCP server, then approve
  it with the passphrase.
- **Claude:** Settings → Connectors → add a custom connector with the same address.

Then, in a chat or in voice mode, say:

> Start a Socratic session on heart failure in Vademecum.

The assistant is the tutor. Each exchange is saved to Vademecum as you go, and the gaps it names
become flags. You'll find the session in the web app under **Tutor → Socratic tutor**.

If the assistant says it cannot see Vademecum's Socratic tools, its connector is out of date.
Refresh it in the connector's settings, or remove and re-add it.

## While the Mac is asleep

On the phone you can still use everything already built: Today, flashcards, board questions,
the encyclopedia, flags, and podcast episodes already voiced. New material waits until the Mac
wakes, reads it and builds from it. The phone's own Socratic tutor uses the Mac's model when
the Mac is awake. When it is asleep, the app hands you to ChatGPT or Claude instead.
