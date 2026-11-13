# Contributing to NetSO

Thanks for helping out!

NetSO is an open-source network lab engine (**Apache-2.0** licensed).
Anyone is welcome to open issues and pull requests.

---

## The golden rule

**Never submit vendor images, firmware, binaries, or licenses.**
NetSO runs images the user already owns. No proprietary artifact may enter
the repository (qcow2/vmdk disks, IOL `.bin`, CML refplat ISOs, `iourc`
keys, etc.). Pull requests containing them will be rejected.

### Clean-room code

- Write **your own** code. Do not paste source from other projects (GNS3 is
  GPL, PNETLab is unlicensed): interoperability with their formats must be
  **re-implemented**, not copied.
- No vendor logos or icons. NetSO's icons are all original
  (`frontend/src/icons.jsx`).
- Do not add license generators (e.g. an `iourc` keygen): NetSO only uses
  the license the user provides, it never creates one.

By opening a pull request you certify the code is yours (or Apache-2.0
compatible) and you contribute it under the project's license.

---

## Development setup

```bash
# Backend (Python 3.10+)
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest                       # tests run in dry-run, no root needed

# Dev server in dry-run (no KVM required)
NETSO_DRY_RUN=1 NETSO_HOME=/tmp/ln NETSO_WEB=../frontend/dist netso serve --port 8080

# Frontend (React + Vite)
cd frontend
npm install
npm run dev                  # UI at :5173 with a proxy to :8080
npm run build                # generates frontend/dist
```

In dry-run mode (`NETSO_DRY_RUN=1`) host commands are only logged, not run:
you can develop without a real Linux server.

---

## Before opening a PR

1. `pytest` passes (backend).
2. `npm run build` passes (frontend).
3. No vendor files or images in the diff.
4. Describe what changes and why.

For large changes, open an issue to discuss it first.
