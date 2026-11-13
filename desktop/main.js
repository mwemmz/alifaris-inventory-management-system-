// Alifaris ERP desktop shell.
// Opens the Django app in a window. Two modes, set in config.json next to this file (or by environment variables):
//   - client:  { "url": "http://server-address:8000" }                      -> connect to a server on your network
//   - local:   { "url": "http://127.0.0.1:8000", "start_server": true,       -> start the bundled Django server first
//                "project_dir": "C:/alifaris/alifaris-inventory-management-system-", "python": "python" }
const { app, BrowserWindow, dialog } = require('electron');
const { spawn } = require('child_process');
const fs = require('fs');
const http = require('http');
const path = require('path');

let cfg = { url: 'http://127.0.0.1:8000', start_server: false, python: 'python', project_dir: '' };
try { cfg = { ...cfg, ...JSON.parse(fs.readFileSync(path.join(__dirname, 'config.json'), 'utf8')) }; } catch (e) { /* defaults */ }
if (process.env.ALIFARIS_URL) cfg.url = process.env.ALIFARIS_URL;

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

function startServer() {
  const port = new URL(cfg.url).port || '8000';
  server = spawn(cfg.python, ['manage.py', 'runserver', `127.0.0.1:${port}`, '--noreload'],
                 { cwd: cfg.project_dir, stdio: 'ignore', windowsHide: true });
  server.on('exit', () => { server = null; });
}

async function createWindow() {
  const win = new BrowserWindow({ width: 1366, height: 768, title: 'Alifaris ERP', autoHideMenuBar: true,
                                  webPreferences: { contextIsolation: true } });
  try {
    if (cfg.start_server) startServer();
    await waitFor(cfg.url);
    await win.loadURL(cfg.url);
  } catch (e) {
    dialog.showErrorBox('Alifaris ERP', `Could not reach ${cfg.url}.\nCheck that the server is running and the address in config.json is right.`);
    app.quit();
  }
}

app.whenReady().then(createWindow);
app.on('window-all-closed', () => { if (server) server.kill(); app.quit(); });
