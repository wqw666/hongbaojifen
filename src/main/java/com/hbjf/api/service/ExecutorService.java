package com.hbjf.api.service;

import com.hbjf.api.dao.RowMapMapper;
import com.hbjf.api.exception.ApiException;
import com.hbjf.api.exception.ErrorCode;
import com.hbjf.api.util.MapBuilder;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * 执行器管理 — 部署在各个 QQ 群的 agent 程序
 * 每个执行器注册后获得唯一 token，通过心跳维持在线状态
 */
@Service
public class ExecutorService {

    private static final DateTimeFormatter FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    private final JdbcTemplate jdbc;

    public ExecutorService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    /** 列表 */
    public List<Map<String, Object>> list(String keyword, String status) {
        StringBuilder sql = new StringBuilder(
                "SELECT id, name, token, status, version, host, group_id, last_heartbeat, created_at FROM executors WHERE 1=1");
        List<Object> args = new java.util.ArrayList<>();
        if (keyword != null && !keyword.isEmpty()) {
            sql.append(" AND (name LIKE ? OR host LIKE ?)");
            args.add("%" + keyword + "%");
            args.add("%" + keyword + "%");
        }
        if (status != null && !status.isEmpty()) {
            sql.append(" AND status = ?");
            args.add(status);
        }
        sql.append(" ORDER BY id DESC");
        return jdbc.query(sql.toString(), new RowMapMapper(), args.toArray());
    }

    /** 新增执行器（自动生成 token） */
    public Map<String, Object> create(String name, String groupId, String version, String host) {
        if (name == null || name.trim().isEmpty()) throw new ApiException(ErrorCode.PARAM_INVALID, "执行器名称不能为空");
        String token = UUID.randomUUID().toString().replace("-", "");
        String now = LocalDateTime.now().format(FMT);
        try {
            jdbc.update("INSERT INTO executors (name, token, status, version, host, group_id, created_at) VALUES (?,?,?,?,?,?,?)",
                    name.trim(), token, "offline", version == null ? "" : version,
                    host == null ? "" : host, groupId == null ? "" : groupId, now);
        } catch (org.springframework.dao.DuplicateKeyException e) {
            throw new ApiException(ErrorCode.PARAM_INVALID, "执行器名称已存在: " + name.trim());
        }
        Map<String, Object> row = jdbc.queryForObject(
                "SELECT id, name, token, status, version, host, group_id, created_at FROM executors WHERE name=?",
                new RowMapMapper(), name.trim());
        row.put("token_plain", token); // 只显示一次，token 不落明文库外
        return row;
    }

    /** 更新基本信息 */
    public void update(Long id, String name, String groupId, String version, String status) {
        if (name == null || name.trim().isEmpty()) throw new ApiException(ErrorCode.PARAM_INVALID, "执行器名称不能为空");
        String now = LocalDateTime.now().format(FMT);
        try {
            jdbc.update("UPDATE executors SET name=?, group_id=?, version=?, status=?, updated_at=? WHERE id=?",
                    name.trim(), groupId == null ? "" : groupId, version == null ? "" : version,
                    status == null ? "offline" : status, now, id);
        } catch (org.springframework.dao.DuplicateKeyException e) {
            throw new ApiException(ErrorCode.PARAM_INVALID, "执行器名称已存在: " + name.trim());
        }
    }

    /** 删除 */
    public void delete(Long id) {
        jdbc.update("DELETE FROM executors WHERE id=?", id);
    }

    /** 重置 token */
    public String resetToken(Long id) {
        String token = UUID.randomUUID().toString().replace("-", "");
        jdbc.update("UPDATE executors SET token=? WHERE id=?", token, id);
        return token;
    }

    /** 心跳（agent 调用）：token 校验 + 更新在线状态 */
    public Map<String, Object> heartbeat(String token, String host, String version, String groupId) {
        if (token == null || token.isEmpty()) throw new ApiException(ErrorCode.NOT_LOGGED_IN, "token不能为空");
        Map<String, Object> exe;
        try {
            exe = jdbc.queryForObject(
                    "SELECT id, name, token, status, group_id FROM executors WHERE token=?",
                    new RowMapMapper(), token);
        } catch (org.springframework.dao.EmptyResultDataAccessException e) {
            exe = null; // 查无此 token，走下方 NOT_FOUND
        }
        if (exe == null) throw new ApiException(ErrorCode.NOT_FOUND, "执行器不存在或token已重置");
        String now = LocalDateTime.now().format(FMT);
        jdbc.update("UPDATE executors SET status='online', host=?, version=?, group_id=?, last_heartbeat=?, updated_at=? WHERE id=?",
                host == null ? "" : host, version == null ? "" : version,
                groupId == null || groupId.isEmpty() ? exe.get("group_id") : groupId, now, now, exe.get("id"));
        return MapBuilder.of("name", exe.get("name"), "server_time", now, "online", true);
    }
}
