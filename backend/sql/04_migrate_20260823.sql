-- 2026-08-23 存量库迁移（B-029）。全新部署不需要：01_schema.sql 已包含这两列。
-- 已初始化过的库执行一次：
--   mysql -uroot energy_tds < backend/sql/04_migrate_20260823.sql
-- 幂等：列已存在时 MariaDB 10.2+ / MySQL 8 的 IF NOT EXISTS 直接跳过（MySQL 8.0 不支持
-- ADD COLUMN IF NOT EXISTS 时会报 1060 Duplicate column，可忽略）。

ALTER TABLE algo_fl_task
  ADD COLUMN IF NOT EXISTS anomaly JSON
    COMMENT 'B-029：算法服务上报的异常 {type,nodeId,round,detail}，gradient_poisoning / privacy_budget_exhausted / training_diverged'
    AFTER trace_id,
  ADD COLUMN IF NOT EXISTS error VARCHAR(512)
    COMMENT 'B-029：任务失败原因（算法服务 error 原文）'
    AFTER anomaly;
