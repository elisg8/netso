# Contribuire a NetSO · Contributing to NetSO

Grazie per voler aiutare! · Thanks for helping out!

NetSO è un motore per lab di rete open source (licenza **Apache-2.0**).
Chiunque può aprire issue e pull request.

NetSO is an open-source network lab engine (**Apache-2.0** licensed).
Anyone is welcome to open issues and pull requests.

---

## Regola fondamentale · The golden rule

**Non inviare mai immagini, firmware, binari o licenze dei vendor.**
NetSO fa girare le immagini che l'utente possiede già: nel repository non
deve finire nessun file proprietario (dischi qcow2/vmdk, `.bin` IOL, ISO
refplat CML, chiavi `iourc`, ecc.). Le pull request che li contengono
verranno rifiutate.

**Never submit vendor images, firmware, binaries, or licenses.**
NetSO runs images the user already owns. No proprietary artifact may enter
the repository (qcow2/vmdk disks, IOL `.bin`, CML refplat ISOs, `iourc`
keys, etc.). Pull requests containing them will be rejected.

### Codice "clean-room" · Clean-room code

- Scrivi codice **tuo**. Non incollare sorgenti presi da altri progetti
  (GNS3 è GPL, PNETLab è senza licenza): l'interoperabilità con i loro
  formati va **reimplementata**, non copiata.
- Niente loghi o icone dei vendor. Le icone di NetSO sono tutte originali
  (`frontend/src/icons.jsx`).
- Non aggiungere generatori di licenza (es. keygen `iourc`): NetSO usa solo
  la licenza fornita dall'utente, non ne crea.

- Write **your own** code. Do not paste source from other projects (GNS3 is
  GPL, PNETLab is unlicensed): interoperability with their formats must be
  **re-implemented**, not copied.
- No vendor logos or icons. NetSO's icons are all original
  (`frontend/src/icons.jsx`).
- Do not add license generators (e.g. an `iourc` keygen): NetSO only uses
  the license the user provides, it never creates one.

Aprendo una pull request dichiari che il codice è tuo (o compatibile con
Apache-2.0) e lo contribuisci sotto la licenza del progetto.
By opening a pull request you certify the code is yours (or Apache-2.0
compatible) and you contribute it under the project's license.

---

## Ambiente di sviluppo · Development setup

```bash
# Backend (Python 3.10+)
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest                       # i test girano in dry-run, non serve root

# Dev server in dry-run (nessun KVM richiesto)
NETSO_DRY_RUN=1 NETSO_HOME=/tmp/ln NETSO_WEB=../frontend/dist netso serve --port 8080

# Frontend (React + Vite)
cd frontend
npm install
npm run dev                  # UI su :5173 con proxy verso :8080
npm run build                # genera frontend/dist
```

In modalità dry-run (`NETSO_DRY_RUN=1`) i comandi di sistema vengono solo
registrati, non eseguiti: puoi sviluppare senza un server Linux reale.

In dry-run mode (`NETSO_DRY_RUN=1`) host commands are only logged, not run:
you can develop without a real Linux server.

---

## Prima di aprire una PR · Before opening a PR

1. `pytest` passa (backend). · `pytest` passes (backend).
2. `npm run build` passa (frontend). · `npm run build` passes (frontend).
3. Nessun file di vendor o immagine nel diff. · No vendor files or images in
   the diff.
4. Descrivi cosa cambia e perché. · Describe what changes and why.

Per cambiamenti grossi, apri prima una issue per discuterne.
For large changes, open an issue to discuss it first.
