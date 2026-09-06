-- ========== 红包积分管理系统 V1.0.8：游戏记录增强（原 V1.0.4 编号与既有迁移冲突，重排补执行） ==========
-- 1) warning_detail：完整异常明细（TEXT 不截断；warning 短字段仍供列表展示）
-- 2) warning_count：异常条数（列表「异常」列展示数量，点开回放看详情）
-- 3) game_record_events.ev_time：事件发生时间（agent 上报 ts；缺省回落服务端时间）

ALTER TABLE game_records ADD COLUMN warning_count INT NOT NULL DEFAULT 0;
ALTER TABLE game_records ADD COLUMN warning_detail TEXT;
ALTER TABLE game_record_events ADD COLUMN ev_time VARCHAR(19) NOT NULL DEFAULT '';
