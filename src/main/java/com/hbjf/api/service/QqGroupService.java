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
 * QQ群管理 — agent 游戏群的落库：正常(active)/封禁(banned，停玩停同步)
 * create_time = QQ 群创建时间（agent 从群信息接口上报，历史数据为空）
 * executor_id = 当前管理该群的执行器（agent 上报时绑定，仅展示用）
 */
@Service
public class QqGroupService {

    private static final DateTimeFormatter FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    private final JdbcTemplate jdbc;

    public QqGroupService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    /** 列表（群号/群名模糊 + 状态过滤） */
    public List<Map<String, Object>> list(String keyword, String status) {
        StringBuilder sql = new StringBuilder(
                "SELECT id, group_id, group_name, create_time, owner_qq, admin_qqs, member_count, status,"
                        + " note, ban_reason, executor_id, created_at, updated_at FROM qq_groups WHERE 1=1");
        List<Object> args = new java.util.ArrayList<>();
        if (keyword != null && !keyword.isEmpty()) {
            sql.append(" AND (group_id LIKE ? OR group_name LIKE ?)");
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

    /** 新增 */
    public void create(String groupId, String groupName, String ownerQq, String adminQqs, String status) {
        if (groupId == null || !groupId.trim().matches("\\d{5,12}")) {
            throw new ApiException(ErrorCode.PARAM_INVALID, "群号格式不正确");
        }
        if (groupName == null || groupName.trim().isEmpty()) {
            throw new ApiException(ErrorCode.PARAM_INVALID, "群名称不能为空");
        }
        String now = LocalDateTime.now().format(FMT);
        try {
            jdbc.update("INSERT INTO qq_groups (group_id, group_name, owner_qq, admin_qqs, status, note, created_at, updated_at)"
                            + " VALUES (?,?,?,?,?,?,?,?)",
                    groupId.trim(), groupName.trim(), ownerQq == null ? "" : ownerQq,
                    adminQqs == null ? "" : adminQqs, normalize(status), "", now, now);
        } catch (org.springframework.dao.DuplicateKeyException e) {
            throw new ApiException(ErrorCode.PARAM_INVALID, "群号已存在: " + groupId.trim());
        }
    }

    /** 更新（名称/群主/管理员/人数/状态/备注；create_time 为空不覆盖） */
    public void update(Long id, String groupName, String ownerQq, String adminQqs,
                       String memberCount, String status, String note) {
        String now = LocalDateTime.now().format(FMT);
        int count = 0;
        if (memberCount != null && !memberCount.isEmpty()) {
            try {
                count = Integer.parseInt(memberCount);
            } catch (NumberFormatException ignored) {
                count = 0;
            }
        }
        jdbc.update("UPDATE qq_groups SET group_name=?, owner_qq=?, admin_qqs=?, member_count=?, status=?,"
                        + " note=COALESCE(NULLIF(?,''),note), updated_at=? WHERE id=?",
                groupName == null ? "" : groupName, ownerQq == null ? "" : ownerQq,
                adminQqs == null ? "" : adminQqs, count, normalize(status),
                note == null ? "" : note, now, id);
    }

    /** 封禁（记录原因与时间） */
    public void ban(Long id, String reason) {
        int rows = jdbc.update("UPDATE qq_groups SET status='banned', ban_reason=?, updated_at=? WHERE id=?",
                reason == null ? "" : reason, LocalDateTime.now().format(FMT), id);
        if (rows == 0) throw new ApiException(ErrorCode.NOT_FOUND, "QQ群不存在");
    }

    /** 解封 */
    public void unban(Long id) {
        int rows = jdbc.update("UPDATE qq_groups SET status='active', ban_reason='', updated_at=? WHERE id=?",
                LocalDateTime.now().format(FMT), id);
        if (rows == 0) throw new ApiException(ErrorCode.NOT_FOUND, "QQ群不存在");
    }

    /** 删除 */
    public void delete(Long id) {
        jdbc.update("DELETE FROM qq_groups WHERE id=?", id);
    }

    /** 按群号查（不存在返回 null） */
    public Map<String, Object> findByGroupId(String groupId) {
        try {
            return jdbc.queryForObject(
                    "SELECT id, group_id, group_name, status FROM qq_groups WHERE group_id=?",
                    new RowMapMapper(), groupId);
        } catch (org.springframework.dao.EmptyResultDataAccessException e) {
            return null;
        }
    }

    private String normalize(String status) {
        if (!"banned".equals(status)) return "active"; // 状态语义收敛为 active/banned，其他一律视为正常
        return "banned";
    }
}
