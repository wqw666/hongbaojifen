-- ========== 红包积分管理系统 V1.0.4 ==========
-- 游戏结算入账 biz_no = game:{round_id}:{qq}，最长 5+96+1+12=114 位，放宽幂等单号列
ALTER TABLE `point_records` MODIFY COLUMN `biz_no` VARCHAR(128) NULL DEFAULT NULL COMMENT '业务单号（幂等；NULL=无单号，不参与唯一约束）';
