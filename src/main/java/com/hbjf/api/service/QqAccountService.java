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
 * 操作员管理 — qq_accounts 只承载管理员QQ（agent 登录号）语义，普通号由 members 承载。
 * 行由 agent 心跳自注册/上报创建；状态(正常/停用)与权限(can_manual_points)由总后台维护，
 * 停用或无权操作员的注册/调分/游戏上报会被守卫拒绝（见 ExecutorService.requireOperable）
 */
@Service
public class QqAccountService {

    private static final DateTimeFormatter FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    private final JdbcTemplate jdbc;

    public QqAccountService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    /** 操作员列表（qq 过滤） */
    public List<Map<String, Object>> list(String qq) {
        StringBuilder sql = new StringBuilder(
                "SELECT id, qq, nickname, status, remark, can_manual_points, last_login_at, last_login_ip, last_host,"
                        + " created_at, updated_at FROM qq_accounts WHERE type='admin_qq'");
        java.util.List<Object> args = new java.util.ArrayList<>();
        if (qq != null && !qq.isEmpty()) {
            sql.append(" AND qq LIKE ?");
            args.add("%" + qq + "%");
        }
        sql.append(" ORDER BY id DESC");
        return jdbc.query(sql.toString(), new RowMapMapper(), args.toArray());
    }

    /** 新增操作员（手动预登记；agent 上线心跳也会自动建行） */
    public void create(String qq, String remark) {
        if (qq == null || !qq.trim().matches("\\d{5,12}")) {
            throw new ApiException(ErrorCode.PARAM_INVALID, "QQ号格式不正确（5-12位数字）");
        }
        String now = LocalDateTime.now().format(FMT);
        try {
            jdbc.update("INSERT INTO qq_accounts (qq, nickname, type, status, remark, can_manual_points, created_at, updated_at)"
                            + " VALUES (?,?,?,?,?,?,?,?)",
                    qq.trim(), "", "admin_qq", "active", remark == null ? "" : remark, "allowed", now, now);
        } catch (org.springframework.dao.DuplicateKeyException e) {
            throw new ApiException(ErrorCode.PARAM_INVALID, "该QQ号已存在: " + qq.trim());
        }
    }

    /** 更新操作员（昵称/备注/状态/手动上下分权限；权限与状态由总后台管理员控制） */
    public void update(Long id, String nickname, String remark, String status, String canManualPoints) {
        String st = "active".equals(status) ? "active" : "disabled";
        String perm = "allowed".equals(canManualPoints) ? "allowed" : "denied";
        String now = LocalDateTime.now().format(FMT);
        int rows = jdbc.update("UPDATE qq_accounts SET nickname=?, remark=?, status=?, can_manual_points=?, updated_at=? WHERE id=?",
                nickname == null ? "" : nickname, remark == null ? "" : remark, st, perm, now, id);
        if (rows == 0) throw new ApiException(ErrorCode.NOT_FOUND, "操作员不存在");
    }

    /** 删除操作员 */
    public void delete(Long id) {
        jdbc.update("DELETE FROM qq_accounts WHERE id=? AND type='admin_qq'", id);
    }
}
