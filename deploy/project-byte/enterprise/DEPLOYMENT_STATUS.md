# September 10 handoff

The personal dashboard update is installed and publicly verified at the existing URL. Its source, data backups and receipt are in `/home/joevps/.local/share/project-byte-private-preview/personal-release-20260910/`. PR #59 records the selective installation. Its execution broker and project workers were not restarted; protected data/registry/credential hashes were unchanged.

The separate invitation site is **prepared, tested, and not deployed**. Desktop Commander rejected the direct privileged provisioning command with `Command not allowed`. This was a connector command-policy rejection, not a request to change project ownership or use another privilege path. No alternate execution path was attempted. A subsequent read confirmed that `/var/lib/prfkt-enterprise`, `/opt/prfkt-enterprise` and all proposed service units do not exist. No new public route was enabled.

Local acceptance passes 32 gateway tests, two private-install integration tests and two enterprise integration tests. The enterprise tests also pass on Linux through Desktop Commander. Mobile (390px) and desktop (1440px) browser checks pass invitation acceptance, blank state, project creation, provider configuration, saved chat after navigation, history and response deduplication with no page errors. Tests use a fake provider, not the owner's subscription. Provisioning and actual systemd isolation remain unverified because the privileged install has not run.

The staged ready release is `/home/joevps/.local/share/prfkt-enterprise-staging/ready-20260910/`. To finish, an authorized VPS terminal must run the prepared command once:

```sh
sudo -n python3 -B /home/joevps/.local/share/prfkt-enterprise-staging/ready-20260910/enterprise/provision.py --source /home/joevps/.local/share/prfkt-enterprise-staging/ready-20260910 --capacity 5 --publish
```

This creates only the new portal and five empty workspaces. It verifies actual filesystem/network isolation, blank inventories, admin sign-in and preservation of personal services before publishing the separate HTTPS port. If any step fails, keep the output and inspect the partial setup; do not rerun or delete its directories to force another attempt.

The intended URL is `https://mcso9tqzb9-1.tailb9395f.ts.net:10000/signin`; it is not yet live. After successful verification, the private admin credential will be in `/home/joevps/.local/share/prfkt-enterprise-access/admin.json`. The owner can create invitations from `/admin`; guest accounts use their own API connections. Execution runners and subscription sign-in are not provided to guests by this pilot.
