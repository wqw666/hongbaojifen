package com.hbjf.api.service;

import com.hbjf.api.exception.ApiException;
import com.hbjf.api.exception.ErrorCode;
import com.hbjf.api.security.JwtUtil;
import com.hbjf.api.util.MapBuilder;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.Map;

/**
 * 认证服务 — 单管理员登录（会员不能登录系统）
 * 首次启动自动创建默认管理员 admin/admin123
 */
@Service
public class AuthService {

    private static final Logger log = LoggerFactory.getLogger(AuthService.class);
    private static final DateTimeFormatter FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    private final JdbcTemplate jdbc;
    private final PasswordEncoder passwordEncoder;
    private final JwtUtil jwtUtil;

    public AuthService(JdbcTemplate jdbc, PasswordEncoder passwordEncoder, JwtUtil jwtUtil) {
        this.jdbc = jdbc;
        this.passwordEncoder = passwordEncoder;
        this.jwtUtil = jwtUtil;
    }

    /** 启动时初始化默认管理员（admin/admin123），已有则跳过 */
    @EventListener(ApplicationReadyEvent.class)
    public void initDefaultAdmin() {
        try {
            Integer c = jdbc.queryForObject("SELECT COUNT(*) FROM admin_users", Integer.class);
            if (c == null || c == 0) {
                String now = LocalDateTime.now().format(FMT);
                jdbc.update("INSERT INTO admin_users (username, password_hash, nickname, created_at, updated_at) VALUES (?,?,?,?,?)",
                        "admin", passwordEncoder.encode("admin123"), "超级管理员", now, now);
                log.info("默认管理员已创建: admin/admin123（请尽快修改）");
            }
        } catch (Exception e) {
            log.warn("初始化默认管理员失败: {}", e.getMessage());
        }
    }

    /** 登录 */
    public Map<String, Object> login(String username, String password) {
        if (username == null || username.isEmpty() || password == null || password.isEmpty()) {
            throw new ApiException(ErrorCode.WRONG_CREDENTIALS, "用户名或密码错误");
        }
        Map<String, Object> admin = findAdmin(username);
        if (admin == null || !passwordEncoder.matches(password, (String) admin.get("password_hash"))) {
            throw new ApiException(ErrorCode.WRONG_CREDENTIALS, "用户名或密码错误");
        }
        String token = jwtUtil.createToken(username);
        return MapBuilder.of("token", token, "username", username, "nickname", admin.get("nickname"));
    }

    /** token 校验（返回当前用户名） */
    public String verify(String token) {
        if (token == null || token.isEmpty()) return null;
        var claims = jwtUtil.verifyToken(token);
        return claims == null ? null : claims.getSubject();
    }

    private Map<String, Object> findAdmin(String username) {
        try {
            return jdbc.queryForMap("SELECT id, username, password_hash, nickname FROM admin_users WHERE username=?", username);
        } catch (org.springframework.dao.EmptyResultDataAccessException e) {
            return null;
        }
    }
}
