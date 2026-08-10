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
tailnet address and MagicDNS name — the MagicDNS name is what step 4 needs, and
the address is worth having to hand for debugging.

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

### 4. Serve a real certificate on the tailnet name

Adding the tailnet address to the existing self-signed certificate's SAN list
looks like the obvious move. It does not work, and the reason is worth
understanding before you spend an evening on it.

#### Why the self-signed certificate cannot cover the tailnet name

iOS grants a relaxed trust policy to **local** network destinations — the one
where an app's `URLSessionDelegate` is allowed to accept a self-signed
certificate at all. It is granted to **single-label hostnames and RFC1918
addresses**, and withheld from **public FQDNs and CGNAT (`100.64.0.0/10`)
addresses**. A MagicDNS name is a public FQDN; a tailnet address is CGNAT. Both
are therefore outside the exemption.

A packet capture shows exactly what that costs. Connecting by the MagicDNS name
or the tailnet IP, the client completes the TCP handshake, sends its
ClientHello, receives the certificate — and then **abandons the connection
without finishing the TLS handshake**. The identical certificate on the same box
is accepted seconds later over the single-label name. The error is
`NSURLErrorDomain -1200` (handshake), **not** `-1202` (trust), because the
rejection happens *below* the layer where the delegate is consulted. No delegate
code, no pinning, and no amount of SAN editing changes it.

So a shared household has no working address at all:

| They try | Result |
| --- | --- |
| the short single-label name | does not resolve — their MagicDNS search domain is *their* tailnet, not yours |
| the MagicDNS FQDN | refused mid-handshake (self-signed on a public FQDN) |
| the tailnet IP | refused mid-handshake (self-signed on CGNAT) |

#### The fix: two certificates, picked by SNI

Tailscale can issue a genuine **Let's Encrypt** certificate for the box's
MagicDNS name (`tailscale cert`). It covers **only that one name** — its SAN
contains that single DNS entry and nothing else — so it cannot replace the
self-signed certificate without breaking the LAN address, the WireGuard address
and the short name.

The box therefore serves **both**, and nginx chooses per connection using the
name in the TLS SNI extension:

| Reached by | Certificate | Trusted how |
| --- | --- | --- |
| single-label name (`my-box`) | self-signed | the app's pin / an installed profile |
| LAN or WireGuard IP | self-signed | same |
| any address with no SNI | self-signed (default block) | same |
| the MagicDNS FQDN | Let's Encrypt | the system trust store — nothing to install |

A connection by **IP address sends no SNI**, so it lands on nginx's
`default_server` — the self-signed block. The tailnet block matches by name
only and can never capture it. With no tailnet certificate installed, no second
block is written at all and the box behaves exactly as it did before.

Configuration lives in `docker/web-nginx.conf` (default block),
`docker/web-server-common.conf` (the body both blocks share) and
`docker/web-render-tailnet-conf.sh` (renders the second block at container
start).

#### What this makes public

Every certificate a public CA issues is published to **certificate
transparency** logs. Issuing one for the MagicDNS name therefore puts
`<box-name>.<tailnet>.ts.net` into a permanent, publicly searchable record. The
operator has accepted this; write it down rather than rediscover it.

What it does and does not mean:

- It reveals that a node with that name exists on that tailnet. Tailnet names
  are frequently derived from an account, so treat the node name as public and
  name it accordingly — this is a reason not to call the box after a person or
  an address (ADR 0030).
- It does **not** make the box reachable. The name resolves to a CGNAT address
  only for devices on the tailnet; there is no public A record, no open port and
  no route in from the internet.

If that trade is unacceptable, the alternative is to keep self-signed only and
accept that shared households cannot use an iOS app — a browser can still be
tapped through the warning.

#### Turn it on

1. Enable HTTPS certificates for the tailnet: admin console → **DNS** → **HTTPS
   Certificates**. MagicDNS must be on.
2. Put the box's MagicDNS name in the box's `.env` (never in the repo — ADR
   0030):

   ```bash
   TLS_TAILNET_NAME=<box-name>.<tailnet>.ts.net
   ```

3. Apply it so the container sees the variable, then issue and install the
   certificate:

   ```bash
   docker compose up -d web
   scripts/tailnet-cert.sh
   ```

   The script runs **on the box**: it asks `tailscaled` for the certificate,
   copies the pair into the `web_certs` volume as `tailnet.crt` / `tailnet.key`,
   renders the second server block, runs `nginx -t`, and reloads. It never
   touches `tls.crt` / `tls.key`, so **no paired device is disturbed**.

4. Check both paths still work — from your own device on the LAN, and over the
   tailnet:

   ```bash
   curl -k https://<box-lan-ip>:8443/api/v1/health        # self-signed, -k needed
   curl    https://<box-magicdns-name>:8443/api/v1/health # CA-signed, no -k
   ```

   The second one succeeding **without** `-k` is the whole point: it means a
   public CA vouches for it and nothing has to be installed on their devices.

#### Renewal is not optional

The Let's Encrypt certificate lasts **90 days**, and the households that depend
on it have no other working address. A silent expiry locks out exactly the
people the feature exists for, and it will happen on an ordinary Tuesday with no
deploy to blame.

`scripts/tailnet-cert.sh` is safe to run on a timer: `tailscale cert` reuses its
cached certificate until renewal is actually due, and an unchanged certificate
is neither copied nor reloaded. Install it as a **daily** systemd timer on the
box — daily rather than monthly so a failure has weeks of retries left in it.

The units are not shipped in the repo because the checkout path and the user
differ per box. Create `/etc/systemd/system/family-cfo-tailnet-cert.service`:

```ini
[Unit]
Description=Renew the Family CFO tailnet TLS certificate
After=tailscaled.service docker.service
Wants=tailscaled.service

[Service]
Type=oneshot
WorkingDirectory=/path/to/family-cfo
ExecStart=/path/to/family-cfo/scripts/tailnet-cert.sh
# Runs as root: tailscaled's socket and the Docker socket both need it.
User=root
```

and `/etc/systemd/system/family-cfo-tailnet-cert.timer`:

```ini
[Unit]
Description=Daily renewal check for the Family CFO tailnet TLS certificate

[Timer]
OnCalendar=daily
RandomizedDelaySec=1h
# Catch up after the box has been off — a missed window must not cost 90 days.
Persistent=true

[Install]
WantedBy=timers.target
```

### The one-command way

```bash
sudo scripts/install-tailnet-cert-timer.sh
```

Writes both unit files with this checkout's paths, enables the timer, and then
**runs the service once and reports whether it actually worked** — which is the
step that matters. Enabling a timer schedules a job; it says nothing about
whether the job succeeds, and a service that errors every night looks identical
to a working one until the certificate expires.

`--check` reports the current state and changes nothing.

`scripts/doctor.sh` checks it stays healthy afterwards, and `scripts/patch.sh`
runs those checks on every deploy — so a renewal that starts failing surfaces at
the next deploy rather than at expiry.

### Or by hand

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now family-cfo-tailnet-cert.timer
systemctl list-timers family-cfo-tailnet-cert.timer   # confirm it is scheduled
sudo systemctl start family-cfo-tailnet-cert.service  # prove it runs clean once
journalctl -u family-cfo-tailnet-cert.service -n 50
```

The service exits non-zero when issuance or the reload fails, so it is worth
alerting on. Whatever you use, check it **before** day 90.

#### Rotation no longer breaks paired devices

The app used to pin whatever certificate it captured at pairing, which was
correct for a self-signed certificate — the fingerprint *is* the trust — and
wrong for a CA-signed one, because its issuer replaces it every ~90 days. A
device paired over the MagicDNS name would have refused the renewed certificate.

Since #86 the app decides per connection, by asking the platform rather than
guessing from the issuer name:

| The server presents | The app does |
| --- | --- |
| a chain reaching a **trusted root**, valid for that host | ordinary system-trust validation — **no pin**, so renewal is invisible |
| a **self-signed** certificate | pins the fingerprint, exactly as before |

So the LAN and WireGuard paths keep the pinning that makes a self-signed
certificate trustworthy, and the tailnet path survives rotation. Nothing to do
at renewal, and nobody re-pairs.

**One caveat that still bites:** this lives in the app, so it only protects a
device running a build that contains it (**0.152.0 or later**). A phone left on
an older build and paired over the MagicDNS name will still refuse the renewed
certificate. Check `Settings → About` on their device before day 90 rather than
after.

#### Which address each person should use

This is the single most useful line in this guide, and the one that costs an
evening if it is wrong:

| Who they are | What they type into the app |
| --- | --- |
| **You** (the tailnet owner), over Tailscale | the **short name** — your search domain resolves it |
| **You**, at home or over WireGuard | the **LAN address** |
| **A shared household** | the **MagicDNS FQDN** — nothing else works for them |

A shared user cannot use the short name (their MagicDNS search domain is their
own tailnet, not yours) and cannot use the tailnet IP (iOS refuses a self-signed
certificate on CGNAT, and the CA-signed one is bound to the name). The FQDN is
their only working address, and it works precisely because step 4 put a
publicly-trusted certificate on it.

#### How they get the app

**TestFlight, not the over-the-air install.** `scripts/deploy-ios-ota.sh`
publishes the build on the box and bakes the **LAN address** into the install
manifest, so the OTA route only works for a device on your home network. A
shared household never is. It is also the path that needs the box's certificate
trusted on the device first, which the CA-signed certificate exists to avoid.

So: `scripts/release-testflight.sh`, then add them as a tester in App Store
Connect. Confirm they are on **0.152.0 or later** — see the caveat above about
what happens at renewal if they are not.

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

Start on the box — `scripts/doctor.sh --setup-only` reports the whole TLS story
in one place: both certificates' expiry, whether the self-signed one is shaped
in a way iOS accepts, whether the SAN covers every address this box answers to,
and whether the renewal **service** is succeeding rather than merely scheduled.
`scripts/patch.sh` runs the same checks after every deploy, so drift surfaces
there rather than at expiry.

Then from one of their devices, with the client connected:

```bash
tailscale status              # the box appears, nothing else does
curl https://<box-magicdns-name>:8443/api/v1/health
```

Expect `{"status":"ok","version":"..."}` — and note the absence of `-k`. If it
only works with `-k`, step 4 has not taken effect and their iPhone app will fail
even though curl is happy; curl accepts what iOS refuses, so a `-k` result
proves nothing. Then confirm the split tunnel is
intact — their public IP should be **unchanged** with the client on:

```bash
curl -s https://api.ipify.org
```

If that returns your home address instead of theirs, an exit node is in play
and must be turned off.
