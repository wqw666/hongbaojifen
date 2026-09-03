-- ========== 红包积分管理系统 初始化（V1） ==========
-- QQ号是会员唯一标识；积分流水正=上分 负=下分；bizNo 幂等防重复

-- 会员表
CREATE TABLE IF NOT EXISTS `members` (
    `id`            BIGINT AUTO_INCREMENT PRIMARY KEY,
    `qq`            VARCHAR(32)  NOT NULL COMMENT 'QQ号（唯一标识）',
    `nickname`      VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '昵称',
    `points`        BIGINT       NOT NULL DEFAULT 0 COMMENT '当前积分',
    `total_income`  BIGINT       NOT NULL DEFAULT 0 COMMENT '累计上分',
    `total_outcome` BIGINT       NOT NULL DEFAULT 0 COMMENT '累计下分',
    `group_id`      VARCHAR(32)  NOT NULL DEFAULT '' COMMENT '来源QQ群号',
    `status`        VARCHAR(16)  NOT NULL DEFAULT 'active' COMMENT 'active正常/disabled停用',
    `note`          VARCHAR(255) NOT NULL DEFAULT '' COMMENT '备注',
    `created_at`    VARCHAR(19)  NOT NULL DEFAULT '',
    `updated_at`    VARCHAR(19)  NOT NULL DEFAULT '',
    UNIQUE KEY `uk_member_qq` (`qq`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 积分流水表
CREATE TABLE IF NOT EXISTS `point_records` (
    `id`         BIGINT AUTO_INCREMENT PRIMARY KEY,
    `qq`         VARCHAR(32)  NOT NULL COMMENT 'QQ号',
    `member_id`  BIGINT       NOT NULL DEFAULT 0 COMMENT '会员id',
    `delta`      BIGINT       NOT NULL COMMENT '积分变动（正=上分 负=下分）',
    `type`       VARCHAR(16)  NOT NULL COMMENT 'INCOME上分/OUTCOME下分',
    `reason`     VARCHAR(255) NOT NULL DEFAULT '' COMMENT '原因',
    `operator`   VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '操作人（admin或执行器名）',
    `biz_no`     VARCHAR(64)  NULL DEFAULT NULL COMMENT '业务单号（幂等；NULL=无单号，不参与唯一约束）',
    `created_at` VARCHAR(19)  NOT NULL DEFAULT '',
    KEY `idx_records_qq` (`qq`),
    KEY `idx_records_created` (`created_at`),
    UNIQUE KEY `uk_records_bizno` (`biz_no`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- QQ号表（type=qq 普通 / admin_qq 管理员）
CREATE TABLE IF NOT EXISTS `qq_accounts` (
    `id`         BIGINT AUTO_INCREMENT PRIMARY KEY,
    `qq`         VARCHAR(32)  NOT NULL COMMENT 'QQ号',
    `nickname`   VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '昵称',
    `type`       VARCHAR(16)  NOT NULL DEFAULT 'qq' COMMENT 'qq普通/admin_qq管理员',
    `status`     VARCHAR(16)  NOT NULL DEFAULT 'active' COMMENT 'active/disabled',
    `remark`     VARCHAR(255) NOT NULL DEFAULT '' COMMENT '备注',
    `created_at` VARCHAR(19)  NOT NULL DEFAULT '',
    `updated_at` VARCHAR(19)  NOT NULL DEFAULT '',
    UNIQUE KEY `uk_qq_accounts_qq` (`qq`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- QQ群表
CREATE TABLE IF NOT EXISTS `qq_groups` (
    `id`           BIGINT AUTO_INCREMENT PRIMARY KEY,
    `group_id`     VARCHAR(32)  NOT NULL COMMENT '群号（唯一）',
    `group_name`   VARCHAR(128) NOT NULL DEFAULT '' COMMENT '群名称',
    `owner_qq`     VARCHAR(32)  NOT NULL DEFAULT '' COMMENT '群主QQ',
    `admin_qqs`    VARCHAR(512) NOT NULL DEFAULT '' COMMENT '管理员QQ（逗号分隔）',
    `member_count` INT          NOT NULL DEFAULT 0 COMMENT '群人数',
    `status`       VARCHAR(16)  NOT NULL DEFAULT 'active' COMMENT 'active正常/paused暂停拉取',
    `note`         VARCHAR(255) NOT NULL DEFAULT '' COMMENT '备注',
    `created_at`   VARCHAR(19)  NOT NULL DEFAULT '',
    `updated_at`   VARCHAR(19)  NOT NULL DEFAULT '',
    UNIQUE KEY `uk_groups_groupid` (`group_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 字典表（AI提示词/系统配置等通用键值）
CREATE TABLE IF NOT EXISTS `dict_items` (
    `id`          BIGINT AUTO_INCREMENT PRIMARY KEY,
    `key`         VARCHAR(64)  NOT NULL COMMENT '字典key',
    `value`       TEXT         NOT NULL COMMENT '字典值',
    `description` VARCHAR(255) NOT NULL DEFAULT '' COMMENT '描述',
    `created_at`  VARCHAR(19)  NOT NULL DEFAULT '',
    `updated_at`  VARCHAR(19)  NOT NULL DEFAULT '',
    UNIQUE KEY `uk_dict_key` (`key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 操作记录表（页面操作审计）
CREATE TABLE IF NOT EXISTS `operation_logs` (
    `id`         BIGINT AUTO_INCREMENT PRIMARY KEY,
    `operator`   VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '操作人',
    `action`     VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '操作（登录/上分/下分/新增等）',
    `target`     VARCHAR(255) NOT NULL DEFAULT '' COMMENT '操作对象',
    `detail`     VARCHAR(1024) NOT NULL DEFAULT '' COMMENT '详情',
    `ip`         VARCHAR(64)  NOT NULL DEFAULT '' COMMENT 'IP',
    `created_at` VARCHAR(19)  NOT NULL DEFAULT '',
    KEY `idx_logs_created` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 执行器表（部署在 QQ 群的 agent 程序）
CREATE TABLE IF NOT EXISTS `executors` (
    `id`             BIGINT AUTO_INCREMENT PRIMARY KEY,
    `name`           VARCHAR(64)  NOT NULL COMMENT '执行器名称（唯一）',
    `token`          VARCHAR(64)  NOT NULL COMMENT '执行器认证token',
    `status`         VARCHAR(16)  NOT NULL DEFAULT 'offline' COMMENT 'online在线/offline离线',
    `version`        VARCHAR(32)  NOT NULL DEFAULT '' COMMENT '程序版本',
    `host`           VARCHAR(128) NOT NULL DEFAULT '' COMMENT '部署主机',
    `group_id`       VARCHAR(32)  NOT NULL DEFAULT '' COMMENT '负责的QQ群号',
    `last_heartbeat` VARCHAR(19)  NOT NULL DEFAULT '' COMMENT '最后心跳时间',
    `note`           VARCHAR(255) NOT NULL DEFAULT '' COMMENT '备注',
    `created_at`     VARCHAR(19)  NOT NULL DEFAULT '',
    `updated_at`     VARCHAR(19)  NOT NULL DEFAULT '',
    UNIQUE KEY `uk_executors_name` (`name`),
    UNIQUE KEY `uk_executors_token` (`token`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 会员玩法规则文件表（文件存磁盘 data/rules/，本表存元数据）
CREATE TABLE IF NOT EXISTS `play_rule_files` (
    `id`          BIGINT AUTO_INCREMENT PRIMARY KEY,
    `name`        VARCHAR(128) NOT NULL COMMENT '玩法名称',
    `description` VARCHAR(255) NOT NULL DEFAULT '' COMMENT '玩法说明',
    `file_name`   VARCHAR(255) NOT NULL COMMENT '磁盘文件名',
    `file_size`   BIGINT       NOT NULL DEFAULT 0 COMMENT '文件大小',
    `version`     VARCHAR(32)  NOT NULL DEFAULT '1.0' COMMENT '版本',
    `status`      VARCHAR(16)  NOT NULL DEFAULT 'active' COMMENT 'active启用/disabled停用',
    `created_at`  VARCHAR(19)  NOT NULL DEFAULT '',
    `updated_at`  VARCHAR(19)  NOT NULL DEFAULT ''
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 管理员表（单管理员，会员不能登录系统）
CREATE TABLE IF NOT EXISTS `admin_users` (
    `id`            BIGINT AUTO_INCREMENT PRIMARY KEY,
    `username`      VARCHAR(64)  NOT NULL COMMENT '用户名',
    `password_hash` VARCHAR(255) NOT NULL COMMENT '密码哈希（BCrypt）',
    `nickname`      VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '昵称',
    `created_at`    VARCHAR(19)  NOT NULL DEFAULT '',
    `updated_at`    VARCHAR(19)  NOT NULL DEFAULT '',
    UNIQUE KEY `uk_admin_username` (`username`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
