-- ========== 红包积分管理系统 V1.0.13：群累计统计 + 流水额分离 ==========
-- 1) 群累计统计：累计对局数 / 累计抽水，结算入账时累加。
--    为什么存数字而不是每次聚合：game_records 走 30 天保留策略（RetentionCleanupService 每 6h 清一次），
--    纯实时聚合的“累计”会随时间缩水；累计值必须落库，近 30 天值才实时聚合（保留期内两者一致）。
ALTER TABLE qq_groups
    ADD COLUMN game_count BIGINT NOT NULL DEFAULT 0 COMMENT '累计对局数（结算时 +1，不随记录清理减少）',
    ADD COLUMN rake_total BIGINT NOT NULL DEFAULT 0 COMMENT '累计抽水（正数，= Σ(-total_delta)）';

-- 回填历史（按现有对局记录聚合；已被清理的更早期记录无法回填，属预期）
UPDATE qq_groups g SET
    game_count = (SELECT COUNT(*) FROM game_records r WHERE r.group_id = g.group_id),
    rake_total = (SELECT COALESCE(SUM(-r.total_delta), 0) FROM game_records r WHERE r.group_id = g.group_id);

-- 2) 流水额与积分变动分离：撑庄模式“下注”按半额记流水（积分仍全额结算，两侧对等）
--    旧数据 flow_amount = delta（旧口径：谁下注谁记全额流水），保持历史流水口径不变
ALTER TABLE point_records
    ADD COLUMN flow_amount BIGINT NOT NULL DEFAULT 0 COMMENT '流水额（撑庄半额；旧数据=delta）';
UPDATE point_records SET flow_amount = delta;

ALTER TABLE game_record_events
    ADD COLUMN flow_amount BIGINT NOT NULL DEFAULT 0 COMMENT '流水额（撑庄半额；旧数据=delta）';
UPDATE game_record_events SET flow_amount = delta;

-- 3) 群维度查询：群列表近 30 天聚合（GROUP BY group_id + created_at 过滤）与记录按群筛选
CREATE INDEX idx_game_records_group_created ON game_records (group_id, created_at);
