/** 浏览器端 MSW worker（main.js 在 VITE_USE_MOCK=true 时启动） */
import { setupWorker } from 'msw/browser'
import { handlers } from './handlers/index.js'
import './db.js'

export const worker = setupWorker(...handlers)
export default worker
