# Private HTTPS iPhone Access

JoeOS supports a private HTTPS route through Tailscale Serve. This is the recommended iPhone/PWA transport because it keeps the command center inside the tailnet while giving Safari and the installed web app a secure HTTPS origin.

Double-click `start_joeos_secure.command` on a Mac or run `start_joeos_secure.sh` on the Halo. The launcher:

1. verifies that Tailscale is installed and connected;
2. binds JoeOS only to `127.0.0.1:8080`;
3. waits for `/healthz`;
4. configures persistent private HTTPS with `tailscale serve --bg --https=443`;
5. prints the tailnet-only HTTPS address.

On the iPhone, connect Tailscale, open the printed `https://...ts.net` address, and choose **Share → Add to Home Screen**. The installed JoeOS icon then opens in standalone mode.

This workflow deliberately uses Tailscale **Serve**, not **Funnel**. Serve obeys tailnet access controls; Funnel would make the endpoint public. Lemonade remains on Halo loopback and is never proxied directly.

The first Serve setup may display an approval link if HTTPS certificates are not enabled for the tailnet. Complete that Tailscale-owned approval once, then run the launcher again. No JoeOS secret is written to a file.

Stopping the launcher stops FastAPI. The private Serve mapping remains configured, so subsequent starts do not require reconfiguration. To remove all Serve mappings intentionally, use Tailscale's own `serve reset` command after reviewing any other services hosted on that device.

Reference: [Tailscale Serve CLI documentation](https://tailscale.com/docs/reference/tailscale-cli/serve).
