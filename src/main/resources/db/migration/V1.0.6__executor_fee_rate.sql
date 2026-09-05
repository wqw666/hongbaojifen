-- ========== 红包积分管理系统 V1.0.6：执行器游戏费率 ==========
-- game_fee_rate：游戏抽水费率（千分比，20=2%）。agent 修改费率后随心跳上报；
-- 管理端「执行器管理」展示该列。

ALTER TABLE executors ADD COLUMN game_fee_rate INT NOT NULL DEFAULT 20 COMMENT '游戏费率千分比(20=2%)';
