// IndexedDB 极简封装：outbox（自增 seq，FIFO）+ cache（键值）
// 无第三方依赖，旧 WebView 兼容（ES2015 语法，构建目标已锁定）
const DB_NAME = 'fs-offline'
const DB_VERSION = 1

function open() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION)
    req.onupgradeneeded = () => {
      const db = req.result
      if (!db.objectStoreNames.contains('outbox')) {
        db.createObjectStore('outbox', { keyPath: 'seq', autoIncrement: true })
      }
      if (!db.objectStoreNames.contains('cache')) {
        db.createObjectStore('cache', { keyPath: 'key' })
      }
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

function run(store, mode, fn) {
  return open().then((db) => new Promise((resolve, reject) => {
    const tx = db.transaction(store, mode)
    const result = fn(tx.objectStore(store))
    tx.oncomplete = () => resolve(result && result._value !== undefined ? result._value : undefined)
    tx.onerror = () => reject(tx.error)
  }))
}

export function dbAdd(store, value) {
  return run(store, 'readwrite', (os) => os.add(value))
}

export function dbPut(store, value) {
  return run(store, 'readwrite', (os) => os.put(value))
}

export function dbDel(store, key) {
  return run(store, 'readwrite', (os) => os.delete(key))
}

export function dbAll(store) {
  return open().then((db) => new Promise((resolve, reject) => {
    const tx = db.transaction(store, 'readonly')
    const req = tx.objectStore(store).getAll()
    req.onsuccess = () => resolve(req.result || [])
    req.onerror = () => reject(req.error)
  }))
}

export function dbGet(store, key) {
  return open().then((db) => new Promise((resolve, reject) => {
    const tx = db.transaction(store, 'readonly')
    const req = tx.objectStore(store).get(key)
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  }))
}
