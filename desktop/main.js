// Alifaris ERP desktop shell.
// Opens the Django app in a window. Two modes, set in config.json next to this file (or by environment variables):
//   - client:  { "url": "http://server-address:8000" }                      -> connect to a server on your network
// Add "mode": "pos" to open straight on the till (/pos/) with the title "Alifaris POS". With "silent_print": true,
// receipts print to the default printer without a dialog (set the receipt printer as the Windows default).
//   - local:   { "url": "http://127.0.0.1:8000", "start_server": true,       -> start the bundled Django server first
//                "project_dir": "C:/alifaris/alifaris-inventory-management-system-", "python": "python" }
const { app, BrowserWindow, dialog } = require('electron');
const { spawn, spawnSync } = require('child_process');
const fs = require('fs');
const http = require('http');
const path = require('path');

let cfg = { host: '', url: 'http://127.0.0.1:8000', start_server: false, python: 'python', project_dir: '', mode: 'erp', silent_print: false };
try { cfg = { ...cfg, ...JSON.parse(fs.readFileSync(path.join(__dirname, 'config.json'), 'utf8')) }; } catch (e) { /* defaults */ }
if (process.env.ALIFARIS_URL) cfg.url = process.env.ALIFARIS_URL;
if (process.env.ALIFARIS_MODE) cfg.mode = process.env.ALIFARIS_MODE;
const isPos = cfg.mode === 'pos';

let server = null;

function waitFor(url, tries = 60) {
  return new Promise((resolve, reject) => {
    const attempt = n => {
      http.get(url, res => { res.resume(); resolve(); })
        .on('error', () => (n <= 0 ? reject(new Error('server did not start')) : setTimeout(() => attempt(n - 1), 500)));
    };
    attempt(tries);
  });
}

// Installed copy: the installer carries its own server program (resources/server), so nothing else is needed on the PC.
const bundled = app.isPackaged
  ? path.join(process.resourcesPath, 'server', process.platform === 'win32' ? 'alifaris-server.exe' : 'alifaris-server')
  : null;
if (bundled) {
  // Installed defaults: run the bundled server on this PC. A till that should use another PC's server puts
  // {"url": "http://shop-pc:8765", "start_server": false} in %APPDATA%/AlifarisERP/desktop.json.
  cfg.url = 'http://127.0.0.1:8765';
  cfg.start_server = true;
  try {
    const f = path.join(process.env.APPDATA || path.join(require('os').homedir(), '.config'), 'AlifarisERP', 'desktop.json');
    cfg = { ...cfg, ...JSON.parse(fs.readFileSync(f, 'utf8')) };
  } catch (e) { /* no override */ }
  if (process.env.ALIFARIS_URL) cfg.url = process.env.ALIFARIS_URL;
}

function startBundled() {
  const port = new URL(cfg.url).port || '8765';
  server = spawn(bundled, ['--port', port, '--host', cfg.host || '127.0.0.1'], { stdio: 'ignore', windowsHide: true });
  server.on('exit', () => { server = null; });
}

function showFirstLogin() {
  // The server writes the first manager login into its data folder once; show it, then remove the file.
  const f = path.join(process.env.APPDATA || path.join(require('os').homedir(), '.config'), 'AlifarisERP', 'first-login.txt');
  try {
    const text = fs.readFileSync(f, 'utf8');
    dialog.showMessageBoxSync({ type: 'info', title: 'Alifaris ERP: your first login',
                                message: 'Write these down. They are shown only once.', detail: text });
    fs.unlinkSync(f);
  } catch (e) { /* not the first run */ }
}

function startServer() {
  if (bundled) return startBundled();
  const port = new URL(cfg.url).port || '8000';
  spawnSync(cfg.python, ['manage.py', 'migrate', '--noinput'], { cwd: cfg.project_dir, stdio: 'ignore', windowsHide: true });
  server = spawn(cfg.python, ['manage.py', 'runserver', `127.0.0.1:${port}`, '--noreload'],
                 { cwd: cfg.project_dir, stdio: 'ignore', windowsHide: true });
  server.on('exit', () => { server = null; });
}

async function createWindow() {
  const win = new BrowserWindow({ width: 1366, height: 768, title: isPos ? 'Alifaris POS' : 'Alifaris ERP', icon: require('path').join(__dirname, 'icon.png'), autoHideMenuBar: true,
                                  webPreferences: { contextIsolation: true } });
  // Receipts open in a new window. Print them straight to the printer when silent_print is on.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (cfg.silent_print && url.includes('/receipt/')) {
      const hidden = new BrowserWindow({ show: false });
      hidden.loadURL(url.replace('?print=1', ''));
      hidden.webContents.once('did-finish-load', () =>
        hidden.webContents.print({ silent: true, printBackground: false }, () => hidden.close()));
      return { action: 'deny' };
    }
    return { action: 'allow' };
  });
  try {
    if (cfg.start_server) startServer();
    await waitFor(cfg.url, 120);
    if (bundled) showFirstLogin();
    await win.loadURL(cfg.url.replace(/\/$/, '') + (isPos ? '/pos/' : '/'));
  } catch (e) {
    dialog.showErrorBox('Alifaris ERP', `Could not reach ${cfg.url}.\nCheck that the server is running and the address in config.json is right.`);
    app.quit();
  }
}

app.whenReady().then(createWindow);
app.on('window-all-closed', () => { if (server) server.kill(); app.quit(); });
