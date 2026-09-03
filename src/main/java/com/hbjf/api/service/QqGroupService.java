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
 * QQ群管理 — 执行器拉人的目标群；admin_qqs 逗号分隔的管理员QQ
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
                "SELECT id, group_id, group_name, owner_qq, admin_qqs, member_count, status, created_at, updated_at FROM qq_groups WHERE 1=1");
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
            jdbc.update("INSERT INTO qq_groups (group_id, group_name, owner_qq, admin_qqs, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                    groupId.trim(), groupName.trim(), ownerQq == null ? "" : ownerQq,
                    adminQqs == null ? "" : adminQqs, status == null ? "active" : status, now, now);
        } catch (org.springframework.dao.DuplicateKeyException e) {
            throw new ApiException(ErrorCode.PARAM_INVALID, "群号已存在: " + groupId.trim());
        }
    }

    /** 更新 */
    public void update(Long id, String groupName, String ownerQq, String adminQqs, String memberCount, String status) {
        String now = LocalDateTime.now().format(FMT);
        int count = 0;
        if (memberCount != null && !memberCount.isEmpty()) {
            try {
                count = Integer.parseInt(memberCount);
            } catch (NumberFormatException ignored) {
                count = 0;
            }
        }
        jdbc.update("UPDATE qq_groups SET group_name=?, owner_qq=?, admin_qqs=?, member_count=?, status=?, updated_at=? WHERE id=?",
                groupName == null ? "" : groupName, ownerQq == null ? "" : ownerQq,
                adminQqs == null ? "" : adminQqs, count, status == null ? "active" : status, now, id);
    }

    /** 删除 */
    public void delete(Long id) {
        jdbc.update("DELETE FROM qq_groups WHERE id=?", id);
    }
}
