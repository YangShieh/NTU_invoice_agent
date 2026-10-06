const { app, BrowserWindow, shell } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const https = require('https');
const http = require('http');

const SERVER_PORT = 8001;
const FRONTEND_DIR = path.join(__dirname, '..', 'frontend');
let serverProcess = null;
let mainWindow = null;

function startServer() {
  serverProcess = spawn('python', ['session_ui.py'], {
    cwd: FRONTEND_DIR,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env }
  });

  serverProcess.stdout.on('data', d => console.log('[Server]', d.toString().trim()));
  serverProcess.stderr.on('data', d => console.error('[Server]', d.toString().trim()));
  serverProcess.on('close', code => {
    console.log(`Server exited with code ${code}`);
    serverProcess = null;
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
  if (serverProcess) {
    serverProcess.kill('SIGTERM');
    serverProcess = null;
  }
  app.quit();
});

app.on('before-quit', () => {
  if (serverProcess) {
    serverProcess.kill('SIGTERM');
    serverProcess = null;
  }
});
