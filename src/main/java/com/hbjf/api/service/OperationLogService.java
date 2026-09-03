package com.hbjf.api.service;

import com.hbjf.api.dao.RowMapMapper;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.List;
import java.util.Map;

/**
 * 操作记录服务 — 页面/接口操作审计
 * 记录：登录、上下分、增删改等关键操作
 */
@Service
public class OperationLogService {

    private static final DateTimeFormatter FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    private final JdbcTemplate jdbc;

    public OperationLogService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    /** 写一条操作日志（不抛异常，记日志失败不影响主流程） */
    public void log(String operator, String action, String target, String detail, String ip) {
        try {
            jdbc.update("INSERT INTO operation_logs (operator, action, target, detail, ip, created_at) VALUES (?,?,?,?,?,?)",
                    operator == null ? "" : operator,
                    action == null ? "" : action,
                    target == null ? "" : target,
                    detail == null ? "" : detail,
                    ip == null ? "" : ip,
                    LocalDateTime.now().format(FMT));
        } catch (Exception ignored) {
        }
    }

    /** 列表（关键词模糊 + 数量限制） */
    public List<Map<String, Object>> list(String keyword, int limit) {
        if (keyword != null && !keyword.isEmpty()) {
            return jdbc.query(
                    "SELECT id, operator, action, target, detail, ip, created_at FROM operation_logs " +
                    "WHERE operator LIKE ? OR target LIKE ? OR detail LIKE ? ORDER BY id DESC LIMIT ?",
                    new RowMapMapper(), "%" + keyword + "%", "%" + keyword + "%", "%" + keyword + "%", Math.min(limit, 1000));
        }
        return jdbc.query(
                "SELECT id, operator, action, target, detail, ip, created_at FROM operation_logs ORDER BY id DESC LIMIT ?",
                new RowMapMapper(), Math.min(limit, 1000));
    }
}
