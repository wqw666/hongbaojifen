-- ========== 红包积分管理系统 V1.0.1：执行器命令通道 ==========
-- 总后台 → agent 远程命令（管理抢红包等）；状态机 pending → sent → done/failed
-- agent poll 拿到 pending 即标记 sent；5 分钟未回报由 poll 重排回 pending 再投递
-- 命令必须幂等（v1 命令集：set_config / sync_members / get_status）

CREATE TABLE IF NOT EXISTS `executor_commands` (
    `id`            BIGINT AUTO_INCREMENT PRIMARY KEY,
    `executor_id`   BIGINT       NOT NULL COMMENT '执行器id',
    `command`       VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '命令名',
    `params`        TEXT         NULL COMMENT '命令参数JSON',
    `status`        VARCHAR(16)  NOT NULL DEFAULT 'pending' COMMENT 'pending待下发/sent已下发待回报/done完成/failed失败',
    `result`        TEXT         NULL COMMENT '执行结果/失败原因',
    `dispatched_at` VARCHAR(19)  NOT NULL DEFAULT '' COMMENT '下发（标记sent）时间，超时重投递依据',
    `created_at`    VARCHAR(19)  NOT NULL DEFAULT '',
    `updated_at`    VARCHAR(19)  NOT NULL DEFAULT '',
    KEY `idx_cmds_executor_status` (`executor_id`, `status`),
    CONSTRAINT `fk_cmds_executor` FOREIGN KEY (`executor_id`) REFERENCES `executors`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
