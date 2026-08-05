# JoeOS Private Runner

The runner is the private execution plane for specifically approved work on a
trusted machine (the Halo computer), connected to the authoritative backend
over a private network such as Tailscale. The VPS never becomes an unrestricted
shell gateway.

## Boundary

- The backend is authoritative. The runner verifies backend-signed job
  envelopes; the backend verifies runner-signed acknowledgement/progress/
  result.
- Models and clients never obtain shell authority and never connect directly
  to the runner.
- Only immutable, approved, policy-revalidated execution jobs are dispatched.
- Execution runs only through registered, capability-scoped executor adapters.
- Secret values are never returned, never placed in jobs/logs/events/artifacts,
  and are redacted from output.

## Package layout

```text
runner/
  joeos_runner/
    __init__.py
    process.py        safe process execution (shell=False, bounded, timeouts)
    executors.py      registered executor adapters
  tests/
    test_process.py   process-safety tests
  README.md
  systemd/            hardened unit example
```

## Runner key handling

- A dedicated P-256 runner signing key is generated locally on the runner.
- The private key never leaves the runner; on Linux it is stored in a file
  readable only by the dedicated runner user with mode 0600.
- Never print or commit the private key.

## Halo installation handoff (placeholders)

Replace `HALO` and machine-specific values. Do not embed real secrets.

```bash
# 1. Create the dedicated unprivileged runner user.
sudo useradd --system --create-home --shell /usr/sbin/nologin joeos-runner

# 2. Fetch JoeOS on the Halo and check out ai-rebuild.
git clone https://github.com/jmw7629/JOEOS.git /opt/joeos
cd /opt/joeos && git checkout ai-rebuild

# 3. Python virtual environment + runner dependencies.
sudo -u joeos-runner python3 -m venv /opt/joeos/runner/.venv
sudo -u joeos-runner /opt/joeos/runner/.venv/bin/pip install --upgrade pip
sudo -u joeos-runner /opt/joeos/runner/.venv/bin/pip install -r requirements.txt

# 4. Runner config directory + permissions.
sudo -u joeos-runner mkdir -p /etc/joeos-runner /var/lib/joeos-runner
sudo chown -R joeos-runner:joeos-runner /etc/joeos-runner /var/lib/joeos-runner

# 5. Confirm Tailscale connectivity to the backend, then on the backend:
python -m server.runners.cli enroll-challenge --fingerprint "<HALO_MACHINE_FINGERPRINT>"

# 6. Enroll on the runner with the one-time challenge (runner CLI), which
#    generates the signing key (0600), signs JOEOS-RUNNER-ENROLLMENT-V1, and
#    binds installation/workspace/machine fingerprint.

# 7. Install the systemd unit (see systemd/joeos-runner.service) and start it.
sudo systemctl daemon-reload
sudo systemctl enable --now joeos-runner

# 8. Verify health from the backend:
python -m server.runners.cli runners
python -m server.runners.cli emergency-stop   # pauses dispatch, cancels queued
```

## Emergency stop

`python -m server.runners.cli emergency-stop` pauses dispatch and cancels
queued jobs without relying on a model or agent. Runner revocation closes
connections and invalidates secret leases immediately.

## Remaining production hardening

- Production enterprise secret manager (interface + encrypted development
  storage are injected; the plaintext retrieval endpoint is never exposed).
- Git/deployment/service executors with real side effects (defined as
  templates; not executed here).
- macOS runner validation, container/VM isolation, and a multi-node fleet.
- Apple build/simulator/physical-device validation.
