-- 迁移脚本：仅用于「已经初始化过的数据库」升级到 2026-08-22 的修复版本。
-- 全新部署无需执行——01_schema.sql 已包含这些定义，MySQL 容器首次启动会自动建成新结构。
--
-- 用法：
--   docker compose exec -T mysql mysql -uroot -p"$MYSQL_ROOT_PASSWORD" energy_tds < backend/sql/03_migrate_20260822.sql
--   （本机开发环境把前半段换成对应的 mysql/mariadb 客户端命令即可）

-- B-011：算法返回的 loss 是 kW² 量级 MSE，DECIMAL(10,6) 在 ≥10000 时溢出，
--        导致整轮训练进度既不落库也不上链。改为 DOUBLE。
ALTER TABLE algo_fl_round
  MODIFY COLUMN loss DOUBLE COMMENT 'B-011：原 DECIMAL(10,6)，kW² 量级 MSE 会溢出导致整轮不落库不上链';

-- B-001 / B-025：篡改演示的原始快照原本只存 Redis（按 key 覆盖且有 TTL），
--        连续演示两次或 Redis 清理后就再也还原不了，链会永久停在 broken 状态。
--        改为落库保存，restore 成功后删除该行。
CREATE TABLE IF NOT EXISTS chain_evidence_backup (
  id            BIGINT PRIMARY KEY AUTO_INCREMENT,
  evidence_id   VARCHAR(64) NOT NULL UNIQUE COMMENT '被篡改的存证 ID',
  payload_snapshot JSON NOT NULL COMMENT '篡改前的原始快照',
  payload_hash  VARCHAR(80) NOT NULL COMMENT '篡改前的 payload_hash',
  block_hash    VARCHAR(80) NOT NULL COMMENT '篡改前的 block_hash',
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='篡改演示的原始快照备份，restore 后删除';
