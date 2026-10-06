const { app, BrowserWindow } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const https = require('https');
const http = require('http');
const SERVER_PORT = 8001;

// Leave null to use the default WSL distribution.
// Otherwise use the exact name from: wsl.exe --list --verbose
const WSL_DISTRO = null;

const WSL_FRONTEND_DIR =
  '/mnt/c/Users/user/Desktop/NTU_invoice_agent/frontend';

const WSL_PYTHON =
  '/home/user/miniconda3/envs/invoice_frontend/bin/python';

function shellQuote(value) {
  return `'${value.replace(/'/g, `'\\''`)}'`;
}

function startServer() {
  const linuxCommand = [
    `cd ${shellQuote(WSL_FRONTEND_DIR)}`,
    `exec ${shellQuote(WSL_PYTHON)} session_ui.py`
  ].join(' && ');

  const wslArguments = [];

  if (WSL_DISTRO) {
    wslArguments.push('-d', WSL_DISTRO);
  }

  wslArguments.push(
    '--',
    'bash',
    '-lc',
    linuxCommand
  );

  console.log('Starting Python server through WSL...');
  console.log(`WSL frontend directory: ${WSL_FRONTEND_DIR}`);
  console.log(`WSL Python: ${WSL_PYTHON}`);
  console.log(`WSL command: ${linuxCommand}`);

  serverProcess = spawn(
    'C:\\Windows\\System32\\wsl.exe',
    wslArguments,
    {
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
      shell: false
    }
  );

  serverProcess.stdout.on('data', data => {
    const output = data.toString().trim();

    if (output) {
      console.log('[Server]', output);
    }
  });

  serverProcess.stderr.on('data', data => {
    const output = data.toString().trim();

    if (output) {
      console.error('[Server]', output);
    }
  });

  serverProcess.on('error', error => {
    console.error('Failed to launch the WSL server:', error);
  });

  serverProcess.on('close', code => {
    console.log(`Server exited with code ${code}`);
    serverProcess = null;

    if (!isQuitting && code !== 0) {
      console.error(
        'The Python server stopped unexpectedly. ' +
        'Check the [Server] messages above.'
      );
    }
  });
}

function tryConnect(protocol, options) {
  return new Promise((resolve, reject) => {
    const request = protocol.get(options, response => {
      // Consume the response so the connection can close cleanly.
      response.resume();
      resolve(true);
    });

    request.on('error', reject);

    request.setTimeout(1000, () => {
      request.destroy(new Error('Connection timed out'));
    });
  });
}

function waitForServer(retries = 60) {
  return new Promise((resolve, reject) => {
    const check = async attempt => {
      if (attempt >= retries) {
        reject(
          new Error(
            `Server did not start on localhost:${SERVER_PORT} ` +
            `within ${retries} seconds`
          )
        );
        return;
      }

      try {
        await tryConnect(https, {
          hostname: 'localhost',
          port: SERVER_PORT,
          path: '/',
          rejectUnauthorized: false
        });

        resolve('https');
        return;
      } catch {
        // The server might be using ordinary HTTP.
      }

      try {
        await tryConnect(http, {
          hostname: 'localhost',
          port: SERVER_PORT,
          path: '/'
        });

        resolve('http');
      } catch {
        setTimeout(() => check(attempt + 1), 1000);
      }
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

  mainWindow.setMenuBarVisibility(false);

  const serverURL = `${protocol}://localhost:${SERVER_PORT}/`;
  mainWindow.loadURL(serverURL);

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.includes('/api/print-page')) {
      const printURL = url.startsWith('http')
        ? url
        : `${protocol}://localhost:${SERVER_PORT}${url}`;

      const printWindow = new BrowserWindow({
        width: 800,
        height: 1100,
        title: '黏存單列印',
        autoHideMenuBar: true,
        webPreferences: {
          nodeIntegration: false,
          contextIsolation: true
        }
      });

      printWindow.setMenuBarVisibility(false);
      printWindow.loadURL(printURL);
    }

    return { action: 'deny' };
  });

  mainWindow.webContents.on('will-navigate', (event, url) => {
    let destination;

    try {
      destination = new URL(url);
    } catch {
      event.preventDefault();
      return;
    }

    const isLocalServer =
      destination.hostname === 'localhost' &&
      Number(destination.port) === SERVER_PORT;

    if (!isLocalServer) {
      event.preventDefault();
    }
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

function stopServer() {
  if (!serverProcess) {
    return;
  }

  console.log('Stopping WSL Python server...');

  try {
    serverProcess.kill('SIGTERM');
  } catch (error) {
    console.error('Failed to stop the WSL server:', error);
  }

  serverProcess = null;
}

app.on(
  'certificate-error',
  (event, webContents, url, error, certificate, callback) => {
    let destination;

    try {
      destination = new URL(url);
    } catch {
      callback(false);
      return;
    }

    // Only allow the self-signed certificate from the local Python server.
    if (
      destination.hostname === 'localhost' &&
      Number(destination.port) === SERVER_PORT
    ) {
      event.preventDefault();
      callback(true);
      return;
    }

    callback(false);
  }
);

app.whenReady().then(async () => {
  startServer();

  try {
    const protocol = await waitForServer();

    console.log(
      `Server ready on ${protocol}://localhost:${SERVER_PORT}`
    );

    createWindow(protocol);
  } catch (error) {
    console.error('Failed to start server:', error);
    isQuitting = true;
    stopServer();
    app.quit();
  }
});

app.on('activate', async () => {
  if (BrowserWindow.getAllWindows().length !== 0) {
    return;
  }

  try {
    const protocol = await waitForServer(5);
    createWindow(protocol);
  } catch (error) {
    console.error('Could not reconnect to server:', error);
  }
});

app.on('window-all-closed', () => {
  isQuitting = true;
  stopServer();
  app.quit();
});

app.on('before-quit', () => {
  isQuitting = true;
  stopServer();
});