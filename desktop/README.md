# Desktop app

## The installer (what you give a client)

A GitHub build makes a normal Windows installer, `Alifaris ERP Setup.exe`. It carries its own server, so the client PC
needs no Python, no Node and no command prompt: double-click, next, and the app opens from the desktop icon.

1. On GitHub open the repo, Actions, "Windows installer", Run workflow. After about 10 minutes download `Alifaris-ERP-Setup`.
2. Run the .exe on the laptop. On the first start it shows an admin login once. Write it down, sign in, then change it
   under Configuration, Users and roles.
3. Data lives in `%APPDATA%\AlifarisERP` (database, secret key, `backups/` with a fresh copy every start, last 14 kept).
   Uninstalling does not delete it. Back that folder up or use Configuration, Data, Download backup.
4. Extra tills: install the same app and create `%APPDATA%\AlifarisERP\desktop.json` containing
   `{"url": "http://SHOP-PC:8765", "start_server": false}`. On the shop PC use `{"host": "0.0.0.0"}` and allow the Windows firewall prompt.

The installer is not code-signed, so Windows SmartScreen shows "unknown publisher": choose More info, Run anyway.
Test the first installer on a spare PC before the client's laptop.

## Developer mode (not for clients)

A thin Electron window around the same Django app.

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
