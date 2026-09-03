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

/**
 * QQ号开放接口服务（agent 管理 QQ 列表）
 * - type=admin_qq：agent 登录的机器人 QQ（群内管理员/发号）
 * - type=qq：普通 QQ 号池
 * upsert 幂等：qq UNIQUE，存在则更新 nickname/remark（非空覆盖），type 不变更
 */
@Service
public class OpenQqAccountService {

    private static final DateTimeFormatter FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    private final JdbcTemplate jdbc;

    public OpenQqAccountService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    /** 上报/更新 QQ 号（返回 created 区分新增/更新） */
    public Map<String, Object> upsert(String qq, String type, String nickname, String remark) {
        if (qq == null || !qq.trim().matches("\\d{5,12}")) {
            throw new ApiException(ErrorCode.PARAM_INVALID, "QQ号格式不正确（5-12位数字）");
        }
        String q = qq.trim();
        String t = "admin_qq".equals(type) ? "admin_qq" : "qq";
        String now = LocalDateTime.now().format(FMT);

        Map<String, Object> exists = findByQq(q);
        if (exists != null) {
            jdbc.update("UPDATE qq_accounts SET nickname=COALESCE(NULLIF(?,''),nickname),"
                            + " remark=COALESCE(NULLIF(?,''),remark), updated_at=? WHERE qq=?",
                    nickname == null ? "" : nickname, remark == null ? "" : remark, now, q);
            return MapBuilder.of("qq", q, "type", t, "created", false);
        }

        try {
            jdbc.update("INSERT INTO qq_accounts (qq, nickname, type, status, remark, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                    q, nickname == null ? "" : nickname, t, "active", remark == null ? "" : remark, now, now);
        } catch (org.springframework.dao.DuplicateKeyException e) {
            // 并发新增兜底：改走更新
            jdbc.update("UPDATE qq_accounts SET nickname=COALESCE(NULLIF(?,''),nickname),"
                            + " remark=COALESCE(NULLIF(?,''),remark), updated_at=? WHERE qq=?",
                    nickname == null ? "" : nickname, remark == null ? "" : remark, now, q);
        }
        return MapBuilder.of("qq", q, "type", t, "created", true);
    }

    /** 列表（type 过滤，默认 qq；含 nickname，供 agent 端展示） */
    public List<Map<String, Object>> list(String type) {
        String t = "admin_qq".equals(type) ? "admin_qq" : "qq";
        return jdbc.query("SELECT id, qq, nickname, type, status, remark, created_at FROM qq_accounts WHERE type=? ORDER BY id",
                new RowMapMapper(), t);
    }

    /** 删除（按 qq，不存在报错） */
    public void deleteByQq(String qq) {
        if (qq == null || !qq.trim().matches("\\d{5,12}")) {
            throw new ApiException(ErrorCode.PARAM_INVALID, "QQ号格式不正确（5-12位数字）");
        }
        int rows = jdbc.update("DELETE FROM qq_accounts WHERE qq=?", qq.trim());
        if (rows == 0) throw new ApiException(ErrorCode.NOT_FOUND, "该QQ号不存在");
    }

    private Map<String, Object> findByQq(String qq) {
        try {
            return jdbc.queryForObject("SELECT id, qq FROM qq_accounts WHERE qq=?",
                    new RowMapMapper(), qq);
        } catch (org.springframework.dao.EmptyResultDataAccessException e) {
            return null;
        }
    }
}
