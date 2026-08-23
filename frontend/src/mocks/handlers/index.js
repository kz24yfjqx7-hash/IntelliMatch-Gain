/** 全部 MSW handlers 汇总（顺序：具体路径在前，带参数路径在后，已在各模块内保证） */
import { authHandlers } from './auth.js'
import { didHandlers } from './did.js'
import { keyHandlers } from './key.js'
import { assetHandlers } from './asset.js'
import { permissionHandlers } from './permission.js'
import { evidenceHandlers } from './evidence.js'
import { auditHandlers } from './audit.js'
import { noticeHandlers } from './notice.js'
import { nodeHandlers } from './node.js'
import { flHandlers } from './fl.js'
import { dispatchHandlers } from './dispatch.js'
import { aiHandlers } from './ai.js'
import { riskHandlers } from './risk.js'

export const handlers = [
  ...authHandlers,
  ...didHandlers,
  ...keyHandlers,
  ...assetHandlers,
  ...permissionHandlers,
  ...evidenceHandlers,
  ...auditHandlers,
  ...noticeHandlers,
  ...nodeHandlers,
  ...flHandlers,
  ...dispatchHandlers,
  ...aiHandlers,
  ...riskHandlers
]

export default handlers
