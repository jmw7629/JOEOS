# September 10 handoff

The personal dashboard update is installed and publicly verified at the existing URL. Its source, data backups and receipt are in `/home/joevps/.local/share/project-byte-private-preview/personal-release-20260910/`. PR #59 records the selective installation. Its execution broker and project workers were not restarted; protected data/registry/credential hashes were unchanged.

The owner ran provisioning from an authorized VPS terminal. The five private workspace services started, but verification failed before publication. A subsequent read confirmed that the portal was restarting with exit status 2, `/opt/prfkt-enterprise` and `/var/lib/prfkt-enterprise` had mode 0700, and new unit files had mode 0600. The installer's 0077 creation mask removed intended read/traverse bits. The personal HTTP services and execution broker retained their PIDs; the port-10000 route is still absent.

The installer now explicitly applies its declared modes. Regression tests cover file/directory modes under a 0077 mask, root DNS configuration, the repair whitelist and exact failure output. The verifier preserves command stderr and exit codes so a failed probe no longer reports only an opaque nested exception. Seven enterprise tests pass locally and on the staged VPS source; the existing browser acceptance and gateway/private-install CI remain applicable.

A narrow repair is staged at `/home/joevps/.local/share/prfkt-enterprise-staging/repair-20260910/`. It validates a fixed list of new enterprise paths, restores only their declared modes, starts the new portal and verifies the running installation before publication. It does not provision accounts, rewrite databases/credentials, change ownership or restart workspace/personal workers. Run this command from the authorized VPS terminal:

```sh
sudo -n python3 -B /home/joevps/.local/share/prfkt-enterprise-staging/repair-20260910/enterprise/repair_permissions.py --publish
```

Do not rerun `provision.py`. If repair fails, preserve its explicit error output and inspect that condition; do not delete the installation or bypass isolation checks. Desktop Commander rejected the direct repair command above with `Command not allowed`; the repair has not been applied. This connector restriction remains the reason for the operator-terminal step. No alternate privilege path was attempted.

The intended URL is `https://mcso9tqzb9-1.tailb9395f.ts.net:10000/signin`; it is not yet live. The generated admin credential already exists in `/home/joevps/.local/share/prfkt-enterprise-access/admin.json`. After successful publication, the owner can create invitations from `/admin`; guests use their own AI API accounts. Execution runners and subscription sign-in are not provided to guests by this pilot.
