package com.hbjf.api.service;

import com.hbjf.api.dao.RowMapMapper;
import com.hbjf.api.exception.ApiException;
import com.hbjf.api.exception.ErrorCode;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.List;
import java.util.Map;

/**
 * 后台用户管理 — admin_users 承载登录总后台的账号。
 * 内置超级管理员 admin 不可删除；用户管理入口（含本服务所有写接口）仅对其开放（见 AdminUserController）。
 * 普通账号可自行改密（AuthController /api/auth/password 走 changeOwnPassword）。
 */
@Service
public class AdminUserService {

    /** 内置超级管理员用户名：种子账号，仅此账号可见/可管「用户管理」，且不可被删除 */
    public static final String BUILTIN_ADMIN = "admin";

    private static final DateTimeFormatter FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");
    private static final int MIN_PASSWORD_LEN = 6;

    private final JdbcTemplate jdbc;
    private final PasswordEncoder passwordEncoder;

    public AdminUserService(JdbcTemplate jdbc, PasswordEncoder passwordEncoder) {
        this.jdbc = jdbc;
        this.passwordEncoder = passwordEncoder;
    }

    /** 用户列表（不含密码哈希；builtin 标注内置账号） */
    public List<Map<String, Object>> list() {
        List<Map<String, Object>> rows = jdbc.query(
                "SELECT id, username, nickname, created_at, updated_at FROM admin_users ORDER BY id",
                new RowMapMapper());
        rows.forEach(r -> r.put("builtin", BUILTIN_ADMIN.equals(r.get("username"))));
        return rows;
    }

    /** 新增后台用户 */
    public void create(String username, String nickname, String password) {
        String name = username == null ? "" : username.trim();
        if (name.isEmpty() || name.length() < 2 || name.length() > 32 || name.contains(" ")) {
            throw new ApiException(ErrorCode.PARAM_INVALID, "用户名需 2-32 个字符且不含空格");
        }
        if (password == null || password.length() < MIN_PASSWORD_LEN) {
            throw new ApiException(ErrorCode.PARAM_INVALID, "密码至少 " + MIN_PASSWORD_LEN + " 位");
        }
        String nick = (nickname == null || nickname.trim().isEmpty()) ? name : nickname.trim();
        String now = LocalDateTime.now().format(FMT);
        try {
            jdbc.update("INSERT INTO admin_users (username, password_hash, nickname, created_at, updated_at) VALUES (?,?,?,?,?)",
                    name, passwordEncoder.encode(password), nick, now, now);
        } catch (org.springframework.dao.DuplicateKeyException e) {
            throw new ApiException(ErrorCode.PARAM_INVALID, "用户名已存在: " + name);
        }
    }

    /** 删除用户；内置 admin 不可删 */
    public void delete(Long id) {
        Map<String, Object> row = findById(id);
        if (BUILTIN_ADMIN.equals(row.get("username"))) {
            throw new ApiException(ErrorCode.PARAM_INVALID, "内置账号 admin 不可删除");
        }
        jdbc.update("DELETE FROM admin_users WHERE id=?", id);
    }

    /** 重置指定用户密码（超级管理员对任意账号，含自己） */
    public void resetPassword(Long id, String password) {
        if (password == null || password.length() < MIN_PASSWORD_LEN) {
            throw new ApiException(ErrorCode.PARAM_INVALID, "密码至少 " + MIN_PASSWORD_LEN + " 位");
        }
        findById(id); // 不存在时报 404
        String now = LocalDateTime.now().format(FMT);
        jdbc.update("UPDATE admin_users SET password_hash=?, updated_at=? WHERE id=?",
                passwordEncoder.encode(password), now, id);
    }

    /** 当前登录用户修改自己的密码（须原密码正确） */
    public void changeOwnPassword(String username, String oldPassword, String newPassword) {
        if (username == null || username.isEmpty()) {
            throw new ApiException(ErrorCode.NOT_LOGGED_IN, "未登录");
        }
        Map<String, Object> row;
        try {
            row = jdbc.queryForMap("SELECT id, password_hash FROM admin_users WHERE username=?", username);
        } catch (org.springframework.dao.EmptyResultDataAccessException e) {
            throw new ApiException(ErrorCode.WRONG_CREDENTIALS, "用户不存在");
        }
        if (oldPassword == null || !passwordEncoder.matches(oldPassword, (String) row.get("password_hash"))) {
            throw new ApiException(ErrorCode.WRONG_CREDENTIALS, "原密码不正确");
        }
        if (newPassword == null || newPassword.length() < MIN_PASSWORD_LEN) {
            throw new ApiException(ErrorCode.PARAM_INVALID, "密码至少 " + MIN_PASSWORD_LEN + " 位");
        }
        String now = LocalDateTime.now().format(FMT);
        jdbc.update("UPDATE admin_users SET password_hash=?, updated_at=? WHERE id=?",
                passwordEncoder.encode(newPassword), now, row.get("id"));
    }

    /** 按 id 查询（不存在 → 404），供删除/重置前确认目标 */
    private Map<String, Object> findById(Long id) {
        try {
            return jdbc.queryForMap("SELECT id, username FROM admin_users WHERE id=?", id);
        } catch (org.springframework.dao.EmptyResultDataAccessException e) {
            throw new ApiException(ErrorCode.NOT_FOUND, "用户不存在");
        }
    }
}
