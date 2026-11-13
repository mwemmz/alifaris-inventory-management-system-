# Desktop app

A thin Electron window around the same Django app. It was written but **not built or run** in the environment this was
created in, so treat the first build as a test.

    cd desktop
    npm install
    npm start          # opens the window (and starts Django if start_server is true)
    npm run dist       # builds a Windows installer with electron-builder

Shop PC (one site): leave `start_server: true`, set `project_dir` to the project folder, and install Python + `pip install -r requirements.txt`.
Counter PCs: set `"start_server": false` and `"url": "http://<shop-server>:8000"`.

## POS till (same app, same database)

The till is part of the same system: `/pos/` in any browser, or this wrapper in `pos` mode so a counter PC opens straight on it.

    npm run start:pos          # or set "mode": "pos" in config.json (see config.pos.example.json)

- **Shop server PC** (runs Django, holds the database): `start_server: true`.
- **Extra tills in the same shop**: `start_server: false`, `url` = the shop server's address. All tills share one stock ledger.
- **A shop that must keep selling with no internet**: the shop server is on the local network, so tills never need the internet. Sites sync to head office whenever the connection is up (Inventory > Configuration > Sync).
- `silent_print: true` sends receipts to the Windows default printer with no dialog (80 mm paper).
- Like the rest of the shell, this has not been built or run here. Test `npm start:pos` on one PC before rolling out.
