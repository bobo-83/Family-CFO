# Giving a Second Household Access (#48)

Family CFO can host more than one family on one box. This guide covers the
networking: letting a household that is not on your LAN reach the box, without
putting the box on the public internet and without routing their ordinary
traffic through your home.

The approach is **Tailscale node sharing**. ADR 0030 applies throughout — the
examples below use placeholders, never real addresses or tailnet names.

## What this buys, and what it costs

The box stays unreachable from the internet: no port forwarding, no dynamic
DNS, no public certificate. Their devices reach it the way yours do over the
VPN — one more admitted device, not an open door. See #2 for why the
"just give them a URL" alternatives are deferred.

The cost is that every one of their family members installs a VPN client and
creates an account before they can look at a budget. Fine for someone
technically comfortable; a real barrier past that.

## Their traffic is not routed through your home

Worth being explicit, because it is the usual worry and the answer is
reassuring: a Tailscale client only routes addresses **inside the tailnet**
(the `100.64.0.0/10` range), plus any subnets a node advertises. Everything
else — their browsing, their streaming, their work VPN — leaves their machine
normally and never touches your network.

Routing all of someone's traffic requires an **exit node**, which must be
advertised on one end and explicitly selected on the other. So the requirement
is met by *not* configuring two things on the box:

- no `--advertise-exit-node`
- no `--advertise-routes` (this one would send their LAN-bound traffic here)

## Run Tailscale on the host, not in the compose stack

The web container publishes 8443 on the host, so a host-level Tailscale gives
the box a tailnet address and everything already listening is reachable there.
No change to `docker-compose.yml`, no change to any image.

As a compose service it would be worse in three ways:

- It needs `NET_ADMIN` and `/dev/net/tun` to create the interface and adjust
  routing — network-level privilege in a stack that currently needs none.
- **A deploy would sever the tunnel.** `scripts/patch.sh` recreates containers,
  so deploying over the tunnel would cut the connection being deployed
  through, mid-run. `patch.sh` already refuses to rebuild `vllm` and `db` for
  neighbouring reasons; needing that same exemption is the tell that it does
  not belong in the stack.
- `docker compose down` should not take remote access with it.

## Sequence

Steps 1–3 need your Tailscale account and root on the box, so they are yours to
run. Step 4 onward can be automated.

### 1. Install on the box

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --hostname=<box-hostname>
```

Follow the printed URL to authenticate the box to your tailnet. Then note its
tailnet address and MagicDNS name — both are needed in step 4.

```bash
tailscale ip -4
tailscale status --json | grep -i dnsname
```

### 2. Share the node with them

In the Tailscale admin console, **share the box node** with their account —
do not add them as a user on your tailnet.

Sharing is one-directional: their devices never join your tailnet, you never
see theirs, and the only thing they can address is the box. Shared users also
do not consume plan seats.

### 3. Restrict them to the one port

The tailnet policy is default-allow between nodes, so this must be written
explicitly. In the access controls, grant the shared user the box on 8443 only
— nothing else on the box, nothing else on the tailnet.

Consider also enabling:

- **Tailnet lock**, if you would rather the coordination server not be able to
  admit a node key on its own. It cannot read traffic either way — WireGuard
  keys never leave the devices — but it does decide which public keys are
  legitimate.
- **Device approval**, so a new device of theirs cannot join unattended.
- **Key expiry**, left at the default rather than disabled.

### 4. Add the tailnet name to the certificate — the disruptive step

**Read this before running it. It breaks every paired iPhone, including
yours.**

The box serves a self-signed certificate whose SAN list is built from
`TLS_CERT_SAN` (`docker/web-entrypoint.sh`). It currently covers the LAN
address and local aliases. Over the tunnel the box is reached by a *different*
address — its tailnet IP and MagicDNS name — and **neither is in the SAN**, so
iOS and Safari reject the connection outright. Modern clients ignore the CN and
validate against the SAN only.

Adding them means regenerating the certificate, and `web-entrypoint.sh` only
generates when none is present, so the existing one must be deleted first. That
changes the certificate's SHA-256 — **which the app pins**
(`FamilyCFOShared/Networking/CertificatePin.swift`; the watch target pins
separately). Every already-paired device must re-pair.

Do it once, deliberately:

1. Add the tailnet IP and MagicDNS name to `TLS_CERT_SAN` in the box's `.env`,
   **together with any other name you expect to want later**, so the pins churn
   a single time.
2. Delete the certificate and restart the web container so it regenerates:

   ```bash
   docker compose exec web rm -f /etc/nginx/certs/tls.crt /etc/nginx/certs/tls.key
   docker compose up -d --force-recreate web
   ```

3. Re-pair your own devices first — confirm the LAN path still works before
   involving anyone else.
4. Then have them install the certificate and pair.

`scripts/deploy-ios-ota.sh` prints the certificate-trust steps their phone
needs.

**This step disappears once #2's mixed-trust work lands.** `tailscale serve`
can terminate TLS with a genuine CA-signed certificate for the MagicDNS name —
no SAN editing, no manual trust, no warning. It is blocked only because those
certificates rotate roughly every 90 days and the app pins fingerprints, so
today it would break every phone about four times a year. After #2 (system
trust store for CA-signed chains, pinning kept for self-signed only), it turns
this section into "open a link".

### 5. Set their household up in the app

- Create the household and invite them (see the onboarding flow).
- **Set its time zone** (#41). This is the case the feature exists for: one box
  cannot have a single "today" that is correct for households in different
  zones. Until it is set, their dates follow `FAMILY_CFO_DEFAULT_TIMEZONE`.
- **Turn on sealed mode** (ADR 0072). Another family's records on shared
  hardware is precisely the situation per-household encryption was designed
  for.

### 6. Decide what you are promising

Their financial records now live on your box, so your backup and uptime
practices are theirs too. Before they depend on it, settle:

- Whether backups cover their household and where those backups live.
- What happens when the box is down, being upgraded, or moving house.
- Who can read what — sealed mode governs data at rest, not what an operator
  with shell access can reach.

Write the answer down somewhere they can see it. See
`docs/guides/backup-and-restore.md` and `docs/guides/disaster-recovery.md`.

## Verifying

From one of their devices, with the client connected:

```bash
tailscale status              # the box appears, nothing else does
curl -k https://<box-tailnet-name>:8443/api/v1/health
```

Expect `{"status":"ok","version":"..."}`. Then confirm the split tunnel is
intact — their public IP should be **unchanged** with the client on:

```bash
curl -s https://api.ipify.org
```

If that returns your home address instead of theirs, an exit node is in play
and must be turned off.
