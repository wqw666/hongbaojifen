-- ========== 红包积分管理系统 V1.0.3 ==========
-- QQ群封禁/创建时间 · 执行器封禁/操作员绑定/来源IP · 会员注册人 · 操作员权限与登录信息 · 游戏记录
--
-- 1) qq_groups：群创建时间(agent 从 QQ 群信息接口上报) / 封禁原因 / 管理执行器(展示用,多执行器共群时最后上报者生效)
--    status 语义收敛：旧 paused(暂停拉取) 废弃 → 一律 active/banned，封禁停玩停同步
ALTER TABLE `qq_groups`
    ADD COLUMN `create_time`  VARCHAR(19)  NOT NULL DEFAULT '' COMMENT '群创建时间(agent上报)' AFTER `group_name`,
    ADD COLUMN `ban_reason`   VARCHAR(255) NOT NULL DEFAULT '' COMMENT '封禁原因' AFTER `note`,
    ADD COLUMN `executor_id`  BIGINT       NOT NULL DEFAULT 0 COMMENT '当前管理执行器id(展示)' AFTER `ban_reason`;
UPDATE `qq_groups` SET `status` = 'active' WHERE `status` = 'paused';

-- 2) executors：一个执行器可管理多个群，去掉单“负责群号”列；加操作员绑定/封禁位/来源IP
ALTER TABLE `executors`
    DROP COLUMN `group_id`,
    ADD COLUMN `admin_qq`   VARCHAR(32)  NOT NULL DEFAULT '' COMMENT '登录的管理员QQ(心跳上报)' AFTER `host`,
    ADD COLUMN `banned_at`  VARCHAR(19)  NOT NULL DEFAULT '' COMMENT '封禁时间(空=未封禁)' AFTER `note`,
    ADD COLUMN `ban_reason` VARCHAR(255) NOT NULL DEFAULT '' COMMENT '封禁原因' AFTER `banned_at`,
    ADD COLUMN `last_ip`    VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '最后心跳来源IP(服务端记录)' AFTER `last_heartbeat`;

-- 3) members：注册人QQ（哪个管理员/操作员把 TA 注册为会员）
ALTER TABLE `members`
    ADD COLUMN `registrar_qq` VARCHAR(32) NOT NULL DEFAULT '' COMMENT '注册人QQ(agent批量注册时)' AFTER `note`;

-- 4) qq_accounts 收窄为操作员(管理员QQ)语义，普通号由 members 承载：
--    权限开关 + 最近登录(在线)信息（QQ 官方不提供登录地理信息，以 agent 上报 IP/主机为准）
ALTER TABLE `qq_accounts`
    ADD COLUMN `can_manual_points` VARCHAR(16) NOT NULL DEFAULT 'allowed' COMMENT 'allowed=可手动上/下分 denied=禁止(总后台可改)' AFTER `remark`,
    ADD COLUMN `last_login_at`     VARCHAR(19)  NOT NULL DEFAULT '' COMMENT '最近登录(在线)时间' AFTER `can_manual_points`,
    ADD COLUMN `last_login_ip`     VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '最近登录来源IP' AFTER `last_login_at`,
    ADD COLUMN `last_host`         VARCHAR(128) NOT NULL DEFAULT '' COMMENT '最近登录主机(机器名)' AFTER `last_login_ip`;

-- 5) 游戏记录：局(round) + 事件明细(回放时间线)；round_id 幂等，入账 biz_no=game:{round_id}:{qq}
CREATE TABLE IF NOT EXISTS `game_records` (
    `id`            BIGINT AUTO_INCREMENT PRIMARY KEY,
    `round_id`      VARCHAR(96)  NOT NULL COMMENT '局号(玩法+群+开赛时间,幂等)',
    `play_id`       BIGINT       NOT NULL DEFAULT 0 COMMENT '玩法id',
    `play_name`     VARCHAR(128) NOT NULL DEFAULT '' COMMENT '玩法名称快照',
    `group_id`      VARCHAR(32)  NOT NULL DEFAULT '' COMMENT '游戏群号',
    `executor_id`   BIGINT       NOT NULL DEFAULT 0 COMMENT '上报执行器id',
    `executor_name` VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '执行器名快照',
    `operator_qq`   VARCHAR(32)  NOT NULL DEFAULT '' COMMENT '操作员QQ',
    `member_count`  INT          NOT NULL DEFAULT 0 COMMENT '结算会员数',
    `total_delta`   BIGINT       NOT NULL DEFAULT 0 COMMENT '本局净变动积分',
    `event_count`   INT          NOT NULL DEFAULT 0 COMMENT '事件数(消息级)',
    `warning`       VARCHAR(512) NOT NULL DEFAULT '' COMMENT '异常说明(如跳过的未知会员)',
    `created_at`    VARCHAR(19)  NOT NULL DEFAULT '',
    UNIQUE KEY `uk_records_round` (`round_id`),
    KEY `idx_records_created` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `game_record_events` (
    `id`         BIGINT AUTO_INCREMENT PRIMARY KEY,
    `record_id`  BIGINT       NOT NULL COMMENT '局id',
    `qq`         VARCHAR(32)  NOT NULL DEFAULT '' COMMENT '玩家QQ',
    `nickname`   VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '昵称',
    `msg`        VARCHAR(512) NOT NULL DEFAULT '' COMMENT '玩家消息(截断)',
    `reply`      VARCHAR(512) NOT NULL DEFAULT '' COMMENT '玩法回复(截断)',
    `delta`      BIGINT       NOT NULL DEFAULT 0 COMMENT '本事件积分(0=无变动)',
    `created_at` VARCHAR(19)  NOT NULL DEFAULT '' COMMENT '事件时间',
    KEY `idx_events_record` (`record_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
