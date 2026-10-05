const { app, BrowserWindow, shell } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const https = require('https');
const http = require('http');

const SERVER_PORT = 8001;
const FRONTEND_DIR = path.join(__dirname, '..', 'frontend');
let serverProcess = null;
let mainWindow = null;
let isQuitting = false;
let restartAttempts = 0;

function writeDiagnostic(stage, outcome, details = {}) {
  try {
    const now = new Date();
    const logDir = path.join(FRONTEND_DIR, 'logs');
    fs.mkdirSync(logDir, { recursive: true });
    const safe = JSON.stringify(details)
      .replace(/\b[A-Z][12]\d{8}\b/g, '[REDACTED_ID]')
      .replace(/\b[A-Z]{2}\d{8}\b/g, '[REDACTED_INVOICE]')
      .replace(/\b\d{10,16}\b/g, '[REDACTED_NUMBER]');
    const record = {
      timestamp_utc: now.toISOString(),
      case_id: 'desktop-process',
      stage,
      outcome,
      details: JSON.parse(safe)
    };
    const day = now.toISOString().slice(0, 10);
    fs.appendFileSync(path.join(logDir, `electron-${day}.jsonl`), `${JSON.stringify(record)}\n`);
    const cutoff = now.getTime() - (30 * 24 * 60 * 60 * 1000);
    for (const name of fs.readdirSync(logDir)) {
      const oldPath = path.join(logDir, name);
      if (name.startsWith('electron-') && fs.statSync(oldPath).mtimeMs < cutoff) {
        fs.unlinkSync(oldPath);
      }
    }
  } catch (_) {
    // Diagnostics must never stop the desktop application.
  }
}

function startServer() {
  const stderrTail = [];
  serverProcess = spawn('python', ['session_ui.py'], {
    cwd: FRONTEND_DIR,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env }
  });

  serverProcess.stdout.on('data', d => console.log('[Server]', d.toString().trim()));
  serverProcess.stderr.on('data', d => {
    const line = d.toString().trim();
    console.error('[Server]', line);
    stderrTail.push(line.slice(-1000));
    if (stderrTail.length > 10) stderrTail.shift();
  });
  serverProcess.on('error', err => {
    writeDiagnostic('desktop.server_spawn', 'failed', { error: String(err) });
  });
  serverProcess.on('close', code => {
    console.log(`Server exited with code ${code}`);
    serverProcess = null;
    if (!isQuitting) {
      writeDiagnostic('desktop.server', 'failed', { exit_code: code, stderr_tail: stderrTail });
      if (restartAttempts < 3) {
        restartAttempts += 1;
        writeDiagnostic('desktop.server_restart', 'started', { attempt: restartAttempts });
        setTimeout(() => {
          startServer();
          waitForServer().then(() => {
            writeDiagnostic('desktop.server_restart', 'completed', { attempt: restartAttempts });
            if (mainWindow && !mainWindow.isDestroyed()) mainWindow.reload();
          }).catch(err => {
            writeDiagnostic('desktop.server_restart', 'failed', {
              attempt: restartAttempts,
              error: String(err)
            });
          });
        }, Math.min(1000 * (2 ** (restartAttempts - 1)), 5000));
      }
    }
  });
}

function waitForServer(retries = 30) {
  return new Promise((resolve, reject) => {
    const check = (attempt) => {
      if (attempt >= retries) return reject(new Error('Server did not start'));

      // Try HTTPS first, then HTTP
      const tryConnect = (protocol, opts) => {
        return new Promise((res, rej) => {
          const req = protocol.get(opts, () => res(true)).on('error', () => rej());
          req.setTimeout(1000, () => { req.destroy(); rej(); });
        });
      };

      tryConnect(https, { hostname: 'localhost', port: SERVER_PORT, path: '/', rejectUnauthorized: false })
        .then(() => resolve('https'))
        .catch(() => {
          tryConnect(http, { hostname: 'localhost', port: SERVER_PORT, path: '/' })
            .then(() => resolve('http'))
            .catch(() => setTimeout(() => check(attempt + 1), 1000));
        });
    };
    check(0);
  });
}

function createWindow(protocol) {
  mainWindow = new BrowserWindow({
    width: 480,
    height: 860,
    title: '希望 - 報帳助理',
    titleBarStyle: 'hiddenInset',
    backgroundColor: '#f5f5f0',
    autoHideMenuBar: true,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true
    }
  });

  // Hide the menu bar completely
  mainWindow.setMenuBarVisibility(false);

  mainWindow.loadURL(`${protocol}://localhost:${SERVER_PORT}/`);

  // Open print page in same window (not external browser)
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.includes('/api/print-page')) {
      // Open print page in a new Electron window
      const printWin = new BrowserWindow({
        width: 800,
        height: 1100,
        title: '黏存單列印',
        autoHideMenuBar: true
      });
      printWin.loadURL(url.startsWith('http') ? url : `${protocol}://localhost:${SERVER_PORT}${url}`);
      return { action: 'deny' };
    }
    return { action: 'deny' };
  });

  // Handle target="_blank" links
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (!url.includes('localhost')) {
      event.preventDefault();
    }
  });

  mainWindow.on('closed', () => { mainWindow = null; });
}

app.whenReady().then(async () => {
  // Allow self-signed SSL cert (must be before any loadURL)
  app.on('certificate-error', (event, webContents, url, error, cert, callback) => {
    event.preventDefault();
    callback(true);
  });

  console.log('Starting server...');
  startServer();

  try {
    const protocol = await waitForServer();
    console.log(`Server ready on ${protocol}://localhost:${SERVER_PORT}`);
    createWindow(protocol);
  } catch (err) {
    console.error('Failed to start server:', err);
    app.quit();
  }
});

app.on('window-all-closed', () => {
  isQuitting = true;
  if (serverProcess) {
    serverProcess.kill('SIGTERM');
    serverProcess = null;
  }
  app.quit();
});

app.on('before-quit', () => {
  isQuitting = true;
  if (serverProcess) {
    serverProcess.kill('SIGTERM');
    serverProcess = null;
  }
});
