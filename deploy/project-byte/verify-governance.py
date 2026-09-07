#!/usr/bin/env python3
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
WF = ROOT / '.github' / 'workflows'
EXPECTED = {
    'project-byte-v4-verify.yml': 'verify-v4',
    'project-byte-installer-verify.yml': 'installer',
    'project-byte-health-runtime-verify.yml': 'verify-health-runtime',
    'project-byte-ux-verify.yml': 'verify-command-center-ux',
    'project-byte-bridge-liveness-verify.yml': 'verify-bridge-liveness',
    'project-byte-active-checkout-verify.yml': 'active-checkout-safety',
    'project-byte-vitros-reconcile-verify.yml': 'verify-vitros-control-reconcile',
    'project-byte-owner-hold-verify.yml': 'verify-owner-hold',
    'project-byte-external-review-verify.yml': 'verify-external-review-observability',
    'project-byte-governance-verify.yml': 'verify-governance-contract',
}
contexts = []
for filename, job in EXPECTED.items():
    text = (WF / filename).read_text()
    lines = text.splitlines()
    try:
        start = lines.index('  pull_request:') + 1
    except ValueError as exc:
        raise SystemExit(f'{filename}: pull_request trigger missing') from exc
    block=[]
    for line in lines[start:]:
        if line.startswith('  ') and not line.startswith('    '): break
        block.append(line)
    if '    branches: [project-byte-deploy]' not in block:
        raise SystemExit(f'{filename}: deploy PR branch missing')
    if any(line.startswith('    paths') for line in block):
        raise SystemExit(f'{filename}: required PR check is path-filtered')
    if not re.search(rf'(?m)^  {re.escape(job)}:\s*$', text):
        raise SystemExit(f'{filename}: expected job context {job!r} missing')
    contexts.append(job)
if len(contexts) != len(set(contexts)):
    raise SystemExit('required status contexts are not unique')
for forbidden in ('jmw7629/StickDeath-Infinity-', 'stickdeath-opencode-bridge.service'):
    for p in WF.glob('project-byte-*.yml'):
        for line in p.read_text().splitlines():
            if forbidden in line and '! grep -q' not in line:
                raise SystemExit(f'positive legacy StickDeath executor reference in {p.name}')
print('DEPLOY_PR_RELEASE_GATES_ALWAYS_PRESENT=PASS')
print('REQUIRED_STATUS_CONTEXTS_UNIQUE=PASS')
print('LEGACY_STICKDEATH_EXECUTOR_REFERENCES=0')
print('CONTEXTS=' + ','.join(contexts))
