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
 * 操作员开放接口服务（agent 上报其登录的管理员QQ；仅 admin_qq 语义，普通号由会员表承载）
 * 注意：常规自注册走 ExecutorService.heartbeat（心跳带 admin_qq 自动建/更新操作员行），
 * 本服务保留给 agent GUI 手动上报/列表/删除用，type 参数兼容忽略
 */
@Service
public class OpenQqAccountService {

    private static final DateTimeFormatter FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    private final JdbcTemplate jdbc;

    public OpenQqAccountService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    /** 上报/更新操作员（返回 created 区分新增/更新；不覆盖权限/状态，那些归总后台管） */
    public Map<String, Object> upsert(String qq, String nickname, String remark) {
        if (qq == null || !qq.trim().matches("\\d{5,12}")) {
            throw new ApiException(ErrorCode.PARAM_INVALID, "QQ号格式不正确（5-12位数字）");
        }
        String q = qq.trim();
        String now = LocalDateTime.now().format(FMT);

        Map<String, Object> exists = findByQq(q);
        if (exists != null) {
            jdbc.update("UPDATE qq_accounts SET type='admin_qq', nickname=COALESCE(NULLIF(?,''),nickname),"
                            + " remark=COALESCE(NULLIF(?,''),remark), updated_at=? WHERE qq=?",
                    nickname == null ? "" : nickname, remark == null ? "" : remark, now, q);
            return MapBuilder.of("qq", q, "type", "admin_qq", "created", false);
        }

        try {
            jdbc.update("INSERT INTO qq_accounts (qq, nickname, type, status, remark, can_manual_points, created_at, updated_at)"
                            + " VALUES (?,?,?,?,?,?,?,?)",
                    q, nickname == null ? "" : nickname, "admin_qq", "active",
                    remark == null ? "" : remark, "allowed", now, now);
        } catch (org.springframework.dao.DuplicateKeyException e) {
            // 并发新增兜底：改走更新
            jdbc.update("UPDATE qq_accounts SET type='admin_qq', nickname=COALESCE(NULLIF(?,''),nickname),"
                            + " remark=COALESCE(NULLIF(?,''),remark), updated_at=? WHERE qq=?",
                    nickname == null ? "" : nickname, remark == null ? "" : remark, now, q);
        }
        return MapBuilder.of("qq", q, "type", "admin_qq", "created", true);
    }

    /** 操作员列表（含权限/登录信息，供 agent GUI 展示） */
    public List<Map<String, Object>> list() {
        return jdbc.query("SELECT id, qq, nickname, status, remark, can_manual_points, last_login_at, last_login_ip, last_host"
                        + " FROM qq_accounts WHERE type='admin_qq' ORDER BY id",
                new RowMapMapper());
    }

    /** 删除（按 qq，不存在报错） */
    public void deleteByQq(String qq) {
        if (qq == null || !qq.trim().matches("\\d{5,12}")) {
            throw new ApiException(ErrorCode.PARAM_INVALID, "QQ号格式不正确（5-12位数字）");
        }
        int rows = jdbc.update("DELETE FROM qq_accounts WHERE qq=? AND type='admin_qq'", qq.trim());
        if (rows == 0) throw new ApiException(ErrorCode.NOT_FOUND, "该操作员QQ不存在");
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
