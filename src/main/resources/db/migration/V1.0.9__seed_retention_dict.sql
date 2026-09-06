-- ========== 红包积分管理系统 V1.0.9：保留天数写入字典（原 V1.0.5 未执行过，重排补执行） ==========
-- 游戏记录默认保留 30 天：写入 dict_items，可在管理端「字典」页调整。
-- RetentionCleanupService 每 6 小时按 game_record_retention_days 清理过期局与事件。
-- 幂等：key 已存在则跳过（不覆盖业务自定值）。

INSERT INTO dict_items (`key`, `value`, `description`, `created_at`, `updated_at`)
SELECT 'game_record_retention_days', '30', '游戏记录保留天数（超期自动清理对局与回放）', NOW(), NOW()
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM dict_items WHERE `key` = 'game_record_retention_days');
