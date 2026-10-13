import { app, BrowserWindow, ipcMain } from 'electron';
import started from 'electron-squirrel-startup';

// Handle creating/removing shortcuts on Windows when installing/uninstalling.
if (started) {
  app.quit();
}

const createWindow = () => {
  // Create the browser window.
  const mainWindow = new BrowserWindow({
    height: 800,
    width: 1280,
    minHeight: 620,
    minWidth: 960,
    frame: false,
    transparent: true,
    backgroundColor: '#00000000',
    resizable: false,
    webPreferences: {
      preload: MAIN_WINDOW_PRELOAD_WEBPACK_ENTRY,
    },
  });

  // Use the available desktop work area with the custom transparent title bar.
  mainWindow.maximize();

  // and load the index.html of the app.
  mainWindow.loadURL(MAIN_WINDOW_WEBPACK_ENTRY);

};

ipcMain.on('renewly:window-control', (event, action) => {
  const window = BrowserWindow.fromWebContents(event.sender);
  if (!window) return;

  if (action === 'minimize') window.minimize();
  if (action === 'toggle-maximize') {
    if (window.isMaximized()) window.unmaximize();
    else window.maximize();
  }
  if (action === 'close') window.close();
});

// This method will be called when Electron has finished
// initialization and is ready to create browser windows.
// Some APIs can only be used after this event occurs.
app.whenReady().then(() => {
  createWindow();

  // On OS X it's common to re-create a window in the app when the
  // dock icon is clicked and there are no other windows open.
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

// Quit when all windows are closed, except on macOS. There, it's common
// for applications and their menu bar to stay active until the user quits
// explicitly with Cmd + Q.
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

// In this file you can include the rest of your app's specific main process
// code. You can also put them in separate files and import them here.
