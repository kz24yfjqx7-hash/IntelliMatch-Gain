-- 2026-08-23 存量库迁移：补 sys_notice 站内消息（铃铛）表。
-- 背景：sys_notice 已在 01_schema.sql（全新部署自动建），但在该表加入之前初始化的存量库缺它，
-- 导致页头/首页拉 GET /notices 返回 500。全新部署不需要执行本脚本。
-- 已初始化过的库执行一次：
--   mysql -uroot energy_tds < backend/sql/05_migrate_20260823.sql
-- 幂等：CREATE TABLE IF NOT EXISTS，重复执行无副作用。
-- 后端 modules/notice/service.py 已加兜底：即使不执行本迁移，站内消息也降级为空而不再 500，
-- 但要真正收到「授权被回收」等站内信，仍需建表。

CREATE TABLE IF NOT EXISTS sys_notice (
  id             BIGINT PRIMARY KEY AUTO_INCREMENT,
  recipient_did  VARCHAR(128) NOT NULL COMMENT '收件人 DID',
  recipient_name VARCHAR(128),
  category       VARCHAR(32)  NOT NULL COMMENT 'permission_apply/permission_result/permission_revoke',
  level          VARCHAR(16)  NOT NULL DEFAULT 'info' COMMENT 'info|success|warning',
  title          VARCHAR(128) NOT NULL,
  content        VARCHAR(512),
  link           VARCHAR(255) COMMENT '点击跳转的前端路由',
  ref_type       VARCHAR(32),
  ref_id         VARCHAR(64),
  actor_did      VARCHAR(128) COMMENT '触发者：申请人或审批人',
  actor_name     VARCHAR(128),
  status         VARCHAR(16)  NOT NULL DEFAULT 'unread' COMMENT 'unread|read',
  read_at        DATETIME,
  trace_id       VARCHAR(64),
  created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_recipient_status (recipient_did, status),
  INDEX idx_recipient_created (recipient_did, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='站内消息（铃铛）';
