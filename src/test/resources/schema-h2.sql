-- H2 测试库结构（与 MySQL 迁移 V1__init.sql + V1.0.1 + V1.0.2 + V1.0.3 保持一致）
CREATE TABLE IF NOT EXISTS members (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, qq VARCHAR(32) NOT NULL UNIQUE,
    nickname VARCHAR(64) DEFAULT '', points BIGINT DEFAULT 0,
    total_income BIGINT DEFAULT 0, total_outcome BIGINT DEFAULT 0,
    group_id VARCHAR(32) DEFAULT '', status VARCHAR(16) DEFAULT 'active',
    note VARCHAR(255) DEFAULT '', registrar_qq VARCHAR(32) DEFAULT '',
    created_at VARCHAR(19) DEFAULT '', updated_at VARCHAR(19) DEFAULT ''
);
CREATE TABLE IF NOT EXISTS point_records (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, qq VARCHAR(32) NOT NULL,
    member_id BIGINT DEFAULT 0, delta BIGINT NOT NULL, type VARCHAR(16) NOT NULL,
    reason VARCHAR(255) DEFAULT '', operator VARCHAR(64) DEFAULT '',
    biz_no VARCHAR(128) DEFAULT NULL, created_at VARCHAR(19) DEFAULT '',
    CONSTRAINT uk_records_bizno UNIQUE (biz_no)
);
CREATE TABLE IF NOT EXISTS qq_accounts (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, qq VARCHAR(32) NOT NULL UNIQUE,
    nickname VARCHAR(64) DEFAULT '', type VARCHAR(16) DEFAULT 'qq',
    status VARCHAR(16) DEFAULT 'active', remark VARCHAR(255) DEFAULT '',
    can_manual_points VARCHAR(16) DEFAULT 'allowed',
    last_login_at VARCHAR(19) DEFAULT '', last_login_ip VARCHAR(64) DEFAULT '',
    last_host VARCHAR(128) DEFAULT '',
    created_at VARCHAR(19) DEFAULT '', updated_at VARCHAR(19) DEFAULT ''
);
CREATE TABLE IF NOT EXISTS qq_groups (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, group_id VARCHAR(32) NOT NULL UNIQUE,
    group_name VARCHAR(128) DEFAULT '', create_time VARCHAR(19) DEFAULT '',
    owner_qq VARCHAR(32) DEFAULT '',
    admin_qqs VARCHAR(512) DEFAULT '', member_count INT DEFAULT 0,
    status VARCHAR(16) DEFAULT 'active', note VARCHAR(255) DEFAULT '',
    ban_reason VARCHAR(255) DEFAULT '', executor_id BIGINT DEFAULT 0,
    created_at VARCHAR(19) DEFAULT '', updated_at VARCHAR(19) DEFAULT ''
);
CREATE TABLE IF NOT EXISTS dict_items (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, "key" VARCHAR(64) NOT NULL UNIQUE,
    "value" TEXT NOT NULL, description VARCHAR(255) DEFAULT '',
    created_at VARCHAR(19) DEFAULT '', updated_at VARCHAR(19) DEFAULT ''
);
CREATE TABLE IF NOT EXISTS operation_logs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, operator VARCHAR(64) DEFAULT '',
    action VARCHAR(64) DEFAULT '', target VARCHAR(255) DEFAULT '',
    detail VARCHAR(1024) DEFAULT '', ip VARCHAR(64) DEFAULT '',
    created_at VARCHAR(19) DEFAULT ''
);
CREATE TABLE IF NOT EXISTS executors (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(64) NOT NULL UNIQUE,
    token VARCHAR(64) NOT NULL UNIQUE, status VARCHAR(16) DEFAULT 'offline',
    version VARCHAR(32) DEFAULT '', host VARCHAR(128) DEFAULT '',
    admin_qq VARCHAR(32) DEFAULT '',
    note VARCHAR(255) DEFAULT '', banned_at VARCHAR(19) DEFAULT '',
    ban_reason VARCHAR(255) DEFAULT '',
    last_heartbeat VARCHAR(19) DEFAULT '', last_ip VARCHAR(64) DEFAULT '',
    created_at VARCHAR(19) DEFAULT '', updated_at VARCHAR(19) DEFAULT ''
);
CREATE TABLE IF NOT EXISTS play_rule_files (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(128) NOT NULL,
    description VARCHAR(255) DEFAULT '', file_name VARCHAR(255) NOT NULL,
    file_size BIGINT DEFAULT 0, version VARCHAR(32) DEFAULT '1.0',
    status VARCHAR(16) DEFAULT 'active',
    created_at VARCHAR(19) DEFAULT '', updated_at VARCHAR(19) DEFAULT ''
);
CREATE TABLE IF NOT EXISTS admin_users (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, username VARCHAR(64) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL, nickname VARCHAR(64) DEFAULT '',
    created_at VARCHAR(19) DEFAULT '', updated_at VARCHAR(19) DEFAULT ''
);
-- V1.0.1 执行器命令通道（与迁移 V1.0.1__executor_commands.sql 一致；FK 级联删命令）
CREATE TABLE IF NOT EXISTS executor_commands (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    executor_id BIGINT NOT NULL,
    command VARCHAR(64) DEFAULT '',
    params TEXT, status VARCHAR(16) DEFAULT 'pending',
    result TEXT, dispatched_at VARCHAR(19) DEFAULT '',
    created_at VARCHAR(19) DEFAULT '', updated_at VARCHAR(19) DEFAULT '',
    CONSTRAINT fk_cmds_executor FOREIGN KEY (executor_id) REFERENCES executors(id) ON DELETE CASCADE
);
-- V1.0.3 游戏记录（局 + 事件明细）
CREATE TABLE IF NOT EXISTS game_records (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, round_id VARCHAR(96) NOT NULL UNIQUE,
    play_id BIGINT DEFAULT 0, play_name VARCHAR(128) DEFAULT '',
    group_id VARCHAR(32) DEFAULT '', executor_id BIGINT DEFAULT 0,
    executor_name VARCHAR(64) DEFAULT '', operator_qq VARCHAR(32) DEFAULT '',
    member_count INT DEFAULT 0, total_delta BIGINT DEFAULT 0, event_count INT DEFAULT 0,
    warning VARCHAR(512) DEFAULT '', warning_count INT DEFAULT 0, warning_detail CLOB DEFAULT '',
    created_at VARCHAR(19) DEFAULT ''
);
CREATE TABLE IF NOT EXISTS game_record_events (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, record_id BIGINT NOT NULL,
    qq VARCHAR(32) DEFAULT '', nickname VARCHAR(64) DEFAULT '',
    msg VARCHAR(512) DEFAULT '', reply VARCHAR(512) DEFAULT '',
    delta BIGINT DEFAULT 0, ev_time VARCHAR(19) DEFAULT '', created_at VARCHAR(19) DEFAULT ''
);

