package com.hbjf.api.service;

import com.hbjf.api.dao.RowMapMapper;
import com.hbjf.api.exception.ApiException;
import com.hbjf.api.exception.ErrorCode;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.List;
import java.util.Map;

/**
 * QQ号管理 — 两个用途：
 *  type=qq        普通 QQ 号池（执行器拉人用的资源）
 *  type=admin_qq  管理员 QQ 号（群内管理/发号）
 */
@Service
public class QqAccountService {

    private static final DateTimeFormatter FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    private final JdbcTemplate jdbc;

    public QqAccountService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    /** 列表（按类型，必填） */
    public List<Map<String, Object>> list(String type, String qq) {
        StringBuilder sql = new StringBuilder(
                "SELECT id, qq, type, remark, created_at FROM qq_accounts WHERE type=?");
        java.util.List<Object> args = new java.util.ArrayList<>();
        args.add(type == null || type.isEmpty() ? "qq" : type);
        if (qq != null && !qq.isEmpty()) {
            sql.append(" AND qq LIKE ?");
            args.add("%" + qq + "%");
        }
        sql.append(" ORDER BY id DESC");
        return jdbc.query(sql.toString(), new RowMapMapper(), args.toArray());
    }

    /** 新增 */
    public void create(String qq, String type, String remark) {
        if (qq == null || !qq.trim().matches("\\d{5,12}")) {
            throw new ApiException(ErrorCode.PARAM_INVALID, "QQ号格式不正确（5-12位数字）");
        }
        String now = LocalDateTime.now().format(FMT);
        try {
            jdbc.update("INSERT INTO qq_accounts (qq, type, remark, created_at) VALUES (?,?,?,?)",
                    qq.trim(), "admin_qq".equals(type) ? "admin_qq" : "qq", remark == null ? "" : remark, now);
        } catch (org.springframework.dao.DuplicateKeyException e) {
            throw new ApiException(ErrorCode.PARAM_INVALID, "该QQ号已存在: " + qq.trim());
        }
    }

    /** 批量新增（逗号/空格/换行分隔） */
    public int createBatch(String qqText, String type, String remark) {
        if (qqText == null || qqText.trim().isEmpty()) {
            throw new ApiException(ErrorCode.PARAM_INVALID, "QQ号不能为空");
        }
        String[] parts = qqText.split("[,\\s\\n;]+");
        int added = 0;
        String now = LocalDateTime.now().format(FMT);
        for (String raw : parts) {
            String qq = raw.trim();
            if (qq.isEmpty()) continue;
            if (!qq.matches("\\d{5,12}")) continue; // 跳过非法行
            try {
                jdbc.update("INSERT INTO qq_accounts (qq, type, remark, created_at) VALUES (?,?,?,?)",
                        qq, "admin_qq".equals(type) ? "admin_qq" : "qq", remark == null ? "" : remark, now);
                added++;
            } catch (org.springframework.dao.DuplicateKeyException ignored) {
                // 已存在跳过
            }
        }
        if (added == 0) throw new ApiException(ErrorCode.PARAM_INVALID, "没有可新增的QQ号（格式不正确或已存在）");
        return added;
    }

    /** 删除 */
    public void delete(Long id) {
        jdbc.update("DELETE FROM qq_accounts WHERE id=?", id);
    }
}
