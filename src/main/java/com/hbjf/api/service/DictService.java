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
 * 字典服务 — 通用键值配置（玩法相关提示词、系统参数等）
 * 管理页 CRUD + key 模糊查询；系统内部用 getValue(key) 读取配置
 */
@Service
public class DictService {

    private static final DateTimeFormatter FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    private final JdbcTemplate jdbc;

    public DictService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    /** 列表（key 模糊匹配，空则全量） */
    public List<Map<String, Object>> list(String key) {
        if (key != null && !key.isEmpty()) {
            return jdbc.query(
                "SELECT id, `key`, `value`, description, created_at, updated_at FROM dict_items WHERE `key` LIKE ? ORDER BY id",
                new RowMapMapper(), "%" + key + "%");
        }
        return jdbc.query(
            "SELECT id, `key`, `value`, description, created_at, updated_at FROM dict_items ORDER BY id",
            new RowMapMapper());
    }

    /** 新增 */
    public void create(String key, String value, String description) {
        if (key == null || key.trim().isEmpty()) throw new ApiException(ErrorCode.PARAM_INVALID, "字典key不能为空");
        if (value == null || value.isEmpty()) throw new ApiException(ErrorCode.PARAM_INVALID, "字典值不能为空");
        String now = LocalDateTime.now().format(FMT);
        try {
            jdbc.update("INSERT INTO dict_items (`key`,`value`,`description`,created_at,updated_at) VALUES (?,?,?,?,?)",
                key.trim(), value, description == null ? "" : description, now, now);
        } catch (org.springframework.dao.DuplicateKeyException e) {
            throw new ApiException(ErrorCode.PARAM_INVALID, "字典key已存在: " + key);
        }
    }

    /** 更新 */
    public void update(Long id, String key, String value, String description) {
        if (key == null || key.trim().isEmpty()) throw new ApiException(ErrorCode.PARAM_INVALID, "字典key不能为空");
        if (value == null || value.isEmpty()) throw new ApiException(ErrorCode.PARAM_INVALID, "字典值不能为空");
        String now = LocalDateTime.now().format(FMT);
        try {
            jdbc.update("UPDATE dict_items SET `key`=?, `value`=?, description=?, updated_at=? WHERE id=?",
                key.trim(), value, description == null ? "" : description, now, id);
        } catch (org.springframework.dao.DuplicateKeyException e) {
            throw new ApiException(ErrorCode.PARAM_INVALID, "字典key已存在: " + key);
        }
    }

    /** 删除 */
    public void delete(Long id) {
        jdbc.update("DELETE FROM dict_items WHERE id=?", id);
    }

    /** 系统内部读取：按 key 取值（不存在返回 null） */
    public String getValue(String key) {
        if (key == null || key.isEmpty()) return null;
        try {
            return jdbc.queryForObject("SELECT `value` FROM dict_items WHERE `key`=?", String.class, key);
        } catch (org.springframework.dao.EmptyResultDataAccessException e) {
            return null;
        }
    }
}
