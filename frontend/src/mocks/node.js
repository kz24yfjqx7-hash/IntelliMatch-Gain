/** Node 端 MSW server（vitest / selfcheck 使用） */
import { setupServer } from 'msw/node'
import { handlers } from './handlers/index.js'
import './db.js'

export const server = setupServer(...handlers)
export { handlers }
export default server
