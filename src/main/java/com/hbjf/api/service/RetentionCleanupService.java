package com.hbjf.api.service;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;

/**
 * 保留期数据清理
 * - 游戏记录（局+事件）默认保留 30 天，配置键 game_record_retention_days
 * - 操作记录默认保留 30 天，配置键 operation_log_retention_days
 * - 每 6 小时跑一次；时间戳为 VARCHAR(19) 字符串，字典序比较可用
 */
@Service
public class RetentionCleanupService {

    private static final DateTimeFormatter FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    private final JdbcTemplate jdbc;
    private final DictService dictService;

    public RetentionCleanupService(JdbcTemplate jdbc, DictService dictService) {
        this.jdbc = jdbc;
        this.dictService = dictService;
    }

    @Scheduled(initialDelay = 300_000, fixedDelay = 6 * 3600_000L)
    public void cleanExpired() {
        try {
            String cutoff = LocalDateTime.now().minusDays(retentionDays("game_record_retention_days"))
                    .format(FMT);
            int eventDeleted = jdbc.update("DELETE FROM game_record_events WHERE record_id IN"
                    + " (SELECT id FROM game_records WHERE created_at < ?)", cutoff);
            int recordDeleted = jdbc.update("DELETE FROM game_records WHERE created_at < ?", cutoff);

            String opCutoff = LocalDateTime.now().minusDays(retentionDays("operation_log_retention_days"))
                    .format(FMT);
            int logDeleted = jdbc.update("DELETE FROM operation_logs WHERE created_at < ?", opCutoff);

            if (recordDeleted + logDeleted > 0) {
                jdbc.update("INSERT INTO operation_logs (operator, action, target, detail, ip, created_at)"
                                + " VALUES (?,?,?,?,?,?)",
                        "system", "清理过期记录", "",
                        "游戏记录删" + recordDeleted + "条(事件" + eventDeleted + ") 操作记录删" + logDeleted + "条",
                        "", LocalDateTime.now().format(FMT));
            }
        } catch (Exception e) {
            // 定时清理失败不影响主流程；下轮再试
            System.err.println("[RetentionCleanup] 清理失败: " + e.getMessage());
        }
    }

    private int retentionDays(String key) {
        String v = dictService.getValue(key);
        if (v == null || v.trim().isEmpty()) return 30;
        try {
            int days = Integer.parseInt(v.trim());
            return Math.max(1, Math.min(days, 3650));
        } catch (NumberFormatException e) {
            return 30;
        }
    }
}
