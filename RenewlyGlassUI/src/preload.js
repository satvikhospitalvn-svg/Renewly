// See the Electron documentation for details on how to use preload scripts:
// https://www.electronjs.org/docs/latest/tutorial/process-model#preload-scripts
import { contextBridge, ipcRenderer } from 'electron';

contextBridge.exposeInMainWorld('renewlyWindow', {
  minimize: () => ipcRenderer.send('renewly:window-control', 'minimize'),
  toggleMaximize: () => ipcRenderer.send('renewly:window-control', 'toggle-maximize'),
  close: () => ipcRenderer.send('renewly:window-control', 'close'),
});
