// Typed allowlist bridge. contextIsolation is on and nodeIntegration is off;
// the renderer gets these calls and nothing else.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('pakon', {
  platform: process.platform,
  backendPort: () => ipcRenderer.invoke('backend-port'),
  openCapture: () => ipcRenderer.invoke('open-capture'),
  openTlxRollFiles: () => ipcRenderer.invoke('open-tlx-roll-files'),
  chooseFolder: (current) => ipcRenderer.invoke('choose-folder', current),
  reveal: (p) => ipcRenderer.invoke('reveal', p),
  openPath: (p) => ipcRenderer.invoke('open-path', p),
  onMenuNewScan: (cb) => {
    const listener = () => cb();
    ipcRenderer.on('menu-new-scan', listener);
    return () => ipcRenderer.removeListener('menu-new-scan', listener);
  },
  onMenuImportBin: (cb) => {
    const listener = () => cb();
    ipcRenderer.on('menu-import-bin', listener);
    return () => ipcRenderer.removeListener('menu-import-bin', listener);
  },
  onMenuImportTlxRoll: (cb) => {
    const listener = () => cb();
    ipcRenderer.on('menu-import-tlx-roll', listener);
    return () => ipcRenderer.removeListener('menu-import-tlx-roll', listener);
  },
});
