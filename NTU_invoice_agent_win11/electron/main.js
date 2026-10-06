const { app, BrowserWindow } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const https = require('https');
const http = require('http');

const SERVER_PORT = 8001;
const EXTERNAL_SERVER =
  process.env.NTU_EXTERNAL_FRONTEND === '1';

// Leave null to use the default WSL distribution.
// Otherwise, use the exact name shown by: wsl.exe --list --verbose
const WSL_DISTRO = null;

const WSL_FRONTEND_DIR =
  '/mnt/c/Users/user/Desktop/NTU_invoice_agent/frontend';

const WSL_PYTHON =
  '/home/user/miniconda3/envs/invoice_frontend/bin/python';

// Directory used for Electron diagnostic logs.
// This does not affect how Python is launched through WSL.
const FRONTEND_DIR = path.join(__dirname, '..', 'frontend');

let serverProcess = null;
let mainWindow = null;
let isQuitting = false;
let restartAttempts = 0;
let restartTimer = null;
let serverGeneration = 0;
let externalServerWasReachable = true;

function shellQuote(value) {
  return `'${value.replace(/'/g, `'\\''`)}'`;
}

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
    const logPath = path.join(logDir, `electron-${day}.jsonl`);

    fs.appendFileSync(
      logPath,
      `${JSON.stringify(record)}\n`
    );

    // Remove Electron diagnostic logs older than 30 days.
    const cutoff =
      now.getTime() - (30 * 24 * 60 * 60 * 1000);

    for (const name of fs.readdirSync(logDir)) {
      if (!name.startsWith('electron-')) {
        continue;
      }

      const oldPath = path.join(logDir, name);
      const stats = fs.statSync(oldPath);

      if (stats.isFile() && stats.mtimeMs < cutoff) {
        fs.unlinkSync(oldPath);
      }
    }
  } catch (_) {
    // Diagnostic logging must never stop the application.
  }
}

function scheduleServerRestart(exitCode, signal, stderrTail) {
  writeDiagnostic('desktop.server', 'failed', {
    exit_code: exitCode,
    signal,
    stderr_tail: stderrTail
  });

  if (
    isQuitting ||
    restartAttempts >= 3 ||
    restartTimer
  ) {
    return;
  }

  restartAttempts += 1;

  const attempt = restartAttempts;
  const delay = Math.min(
    1000 * (2 ** (attempt - 1)),
    5000
  );

  writeDiagnostic(
    'desktop.server_restart',
    'started',
    {
      attempt,
      delay_ms: delay
    }
  );

  restartTimer = setTimeout(async () => {
    restartTimer = null;

    if (isQuitting) {
      return;
    }

    startServer();

    const expectedGeneration = serverGeneration;

    try {
      await waitForServer();

      // A later restart may already have replaced this process.
      if (
        isQuitting ||
        expectedGeneration !== serverGeneration
      ) {
        return;
      }

      writeDiagnostic(
        'desktop.server_restart',
        'completed',
        { attempt }
      );

      if (
        mainWindow &&
        !mainWindow.isDestroyed()
      ) {
        mainWindow.reload();
      }
    } catch (error) {
      if (expectedGeneration === serverGeneration) {
        writeDiagnostic(
          'desktop.server_restart',
          'failed',
          {
            attempt,
            error: String(error)
          }
        );
      }
    }
  }, delay);
}

function startServer() {
  const stderrTail = [];

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
  console.log(
    `WSL frontend directory: ${WSL_FRONTEND_DIR}`
  );
  console.log(`WSL Python: ${WSL_PYTHON}`);
  console.log(`WSL command: ${linuxCommand}`);

  const launchedProcess = spawn(
    'C:\\Windows\\System32\\wsl.exe',
    wslArguments,
    {
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
      shell: false
    }
  );

  serverProcess = launchedProcess;
  serverGeneration += 1;

  launchedProcess.stdout.on('data', data => {
    const output = data.toString().trim();

    if (output) {
      console.log('[Server]', output);
    }
  });

  launchedProcess.stderr.on('data', data => {
    const output = data.toString().trim();

    if (output) {
      console.error('[Server]', output);

      stderrTail.push(output.slice(-1000));

      if (stderrTail.length > 10) {
        stderrTail.shift();
      }
    }
  });

  launchedProcess.on('error', error => {
    console.error(
      'Failed to launch the WSL server:',
      error
    );

    writeDiagnostic(
      'desktop.server_spawn',
      'failed',
      {
        error: String(error)
      }
    );
  });

  launchedProcess.on('close', (code, signal) => {
    console.log(`Server exited with code ${code}`);

    if (serverProcess === launchedProcess) {
      serverProcess = null;
    }

    if (!isQuitting) {
      console.error(
        'The Python server stopped unexpectedly. ' +
        'Attempting an automatic restart.'
      );

      scheduleServerRestart(
        code,
        signal,
        stderrTail
      );
    }
  });
}

function tryConnect(protocol, options) {
  return new Promise((resolve, reject) => {
    const request = protocol.get(
      options,
      response => {
        // Consume the response so the connection closes cleanly.
        response.resume();
        resolve(true);
      }
    );

    request.on('error', reject);

    request.setTimeout(1000, () => {
      request.destroy(
        new Error('Connection timed out')
      );
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
        setTimeout(
          () => check(attempt + 1),
          1000
        );
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

  const serverURL =
    `${protocol}://localhost:${SERVER_PORT}/`;

  mainWindow.loadURL(serverURL);

  mainWindow.webContents.setWindowOpenHandler(
    ({ url }) => {
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
    }
  );

  mainWindow.webContents.on(
    'will-navigate',
    (event, url) => {
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
    }
  );

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

function stopServer() {
  if (restartTimer) {
    clearTimeout(restartTimer);
    restartTimer = null;
  }

  if (!serverProcess) {
    return;
  }

  console.log('Stopping WSL Python server...');

  try {
    serverProcess.kill('SIGTERM');
  } catch (error) {
    console.error(
      'Failed to stop the WSL server:',
      error
    );
  }

  serverProcess = null;
}

app.on(
  'certificate-error',
  (
    event,
    webContents,
    url,
    error,
    certificate,
    callback
  ) => {
    let destination;

    try {
      destination = new URL(url);
    } catch {
      callback(false);
      return;
    }

    // Only accept the self-signed certificate
    // from the local Python server.
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
  if (!EXTERNAL_SERVER) {
    startServer();
  } else {
    console.log(
      'Using the frontend server managed by START_WIN11.bat.'
    );
  }

  try {
    const protocol = await waitForServer();

    console.log(
      `Server ready on ` +
      `${protocol}://localhost:${SERVER_PORT}`
    );

    createWindow(protocol);
  } catch (error) {
    console.error(
      'Failed to start server:',
      error
    );

    isQuitting = true;
    stopServer();
    app.quit();
  }
});

// When the batch supervisor restarts the external frontend, reload the
// Electron window automatically as soon as port 8001 is healthy again.
setInterval(async () => {
  if (!EXTERNAL_SERVER || isQuitting) {
    return;
  }

  try {
    const protocol = await waitForServer(2);

    if (
      !externalServerWasReachable &&
      mainWindow &&
      !mainWindow.isDestroyed()
    ) {
      mainWindow.loadURL(
        `${protocol}://localhost:${SERVER_PORT}/`
      );
    }

    externalServerWasReachable = true;
  } catch {
    externalServerWasReachable = false;
  }
}, 3000);

app.on('activate', async () => {
  if (BrowserWindow.getAllWindows().length !== 0) {
    return;
  }

  try {
    const protocol = await waitForServer(5);
    createWindow(protocol);
  } catch (error) {
    console.error(
      'Could not reconnect to server:',
      error
    );
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
