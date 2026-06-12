# Deploy

Single-node Linux host, systemd `--user` service behind Caddy. Same pattern as `ai-index`.

## 0. Prereqs

- DNS: add `A` (or `CNAME`) record `ai-datacenters.lowlandcapital.com` → your server IP.
- xAI API key: get one at <https://console.x.ai/>. Note the spend rate ($1.25/M input, $2.50/M output for `grok-4.3`).
- `uv` installed for the deploy user.

## 1. Get the code

```bash
cd ~
git clone https://github.com/RTB007/ai-datacenters.git
cd ai-datacenters
uv sync
```

## 2. Initialize the DB (optional — first request also does this)

```bash
uv run python -c "from ai_datacenters import config, db; db.init(config.db_path(config.load()))"
```

## 3. Install the systemd user service

```bash
mkdir -p ~/.config/systemd/user
cp deploy/ai-datacenters.service ~/.config/systemd/user/
# Edit the file and replace REPLACE_ME with your real xAI API key
$EDITOR ~/.config/systemd/user/ai-datacenters.service

systemctl --user daemon-reload
systemctl --user enable --now ai-datacenters.service
systemctl --user status ai-datacenters.service

# Make sure user services keep running after logout:
loginctl enable-linger $USER
```

App should now be reachable at `http://127.0.0.1:8088`.

## 4. Caddy

Append [`Caddyfile.snippet`](./Caddyfile.snippet) to your global Caddyfile and reload:

```bash
sudo systemctl reload caddy
```

Open <https://ai-datacenters.lowlandcapital.com/> in a browser. First load shows
an empty table — click **Discover projects** to seed it.

## 5. Logs

```bash
journalctl --user -u ai-datacenters -f
```

## Rotating the API key

Edit the unit file, then:
```bash
systemctl --user daemon-reload
systemctl --user restart ai-datacenters
```
