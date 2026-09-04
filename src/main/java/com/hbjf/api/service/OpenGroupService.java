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
 * QQ群开放接口服务（agent 上报群信息）
 * upsert：存在则更新（仅覆盖上报字段，note/status 不碰，防空串冲掉既有值）；
 * 不存在则插入。list 委托 QqGroupService 既有读路径。
 */
@Service
public class OpenGroupService {

    private static final DateTimeFormatter FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    private final JdbcTemplate jdbc;
    private final QqGroupService qqGroupService;

    public OpenGroupService(JdbcTemplate jdbc, QqGroupService qqGroupService) {
        this.jdbc = jdbc;
        this.qqGroupService = qqGroupService;
    }

    /**
     * 上报/更新群信息（group_id 必填；返回 created 区分新增/更新）
     * @param executorId 上报执行器 id（>=1 时绑定为当前管理执行器，展示用）
     * @param createTime 群创建时间（QQ 群信息接口值；空不覆盖已有值）
     */
    public Map<String, Object> upsert(String groupId, String groupName, String ownerQq,
                                      String adminQqs, String memberCount, String note,
                                      Long executorId, String createTime) {
        if (groupId == null || !groupId.trim().matches("\\d{5,12}")) {
            throw new ApiException(ErrorCode.PARAM_INVALID, "群号格式不正确");
        }
        String gid = groupId.trim();
        String now = LocalDateTime.now().format(FMT);

        // member_count：可解析才生效，否则不更新（新增默认 0）
        Integer count = null;
        if (memberCount != null && !memberCount.isEmpty()) {
            try {
                count = Integer.parseInt(memberCount.trim());
            } catch (NumberFormatException ignored) {
                count = null;
            }
        }

        // create_time 只接受 yyyy-MM-dd HH:mm:ss 格式（防空串/脏值覆盖真值）
        String ct = normalizeCreateTime(createTime);
        Long exeId = executorId == null ? 0L : executorId;

        Map<String, Object> exists = findByGroupId(gid);
        if (exists != null) {
            jdbc.update("UPDATE qq_groups SET group_name=COALESCE(NULLIF(?,''),group_name),"
                            + " owner_qq=COALESCE(NULLIF(?,''),owner_qq),"
                            + " admin_qqs=COALESCE(NULLIF(?,''),admin_qqs),"
                            + " member_count=COALESCE(?,member_count),"
                            + " create_time=COALESCE(NULLIF(?,''),create_time),"
                            + " executor_id=CASE WHEN ? > 0 THEN ? ELSE executor_id END,"
                            + " updated_at=? WHERE group_id=?",
                    groupName == null ? "" : groupName, ownerQq == null ? "" : ownerQq,
                    adminQqs == null ? "" : adminQqs, count, ct, exeId, exeId, now, gid);
            return MapBuilder.of("group_id", gid, "created", false);
        }

        try {
            jdbc.update("INSERT INTO qq_groups (group_id, group_name, owner_qq, admin_qqs, member_count, status,"
                            + " create_time, note, executor_id, created_at, updated_at)"
                            + " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    gid, groupName == null ? "" : groupName, ownerQq == null ? "" : ownerQq,
                    adminQqs == null ? "" : adminQqs, count == null ? 0 : count, "active",
                    ct, note == null ? "" : note, exeId, now, now);
        } catch (org.springframework.dao.DuplicateKeyException e) {
            // 并发新增兜底：改走更新
            jdbc.update("UPDATE qq_groups SET group_name=COALESCE(NULLIF(?,''),group_name),"
                            + " owner_qq=COALESCE(NULLIF(?,''),owner_qq),"
                            + " admin_qqs=COALESCE(NULLIF(?,''),admin_qqs),"
                            + " member_count=COALESCE(?,member_count),"
                            + " create_time=COALESCE(NULLIF(?,''),create_time),"
                            + " executor_id=CASE WHEN ? > 0 THEN ? ELSE executor_id END,"
                            + " updated_at=? WHERE group_id=?",
                    groupName == null ? "" : groupName, ownerQq == null ? "" : ownerQq,
                    adminQqs == null ? "" : adminQqs, count, ct, exeId, exeId, now, gid);
        }
        return MapBuilder.of("group_id", gid, "created", true);
    }

    private String normalizeCreateTime(String createTime) {
        if (createTime == null || createTime.trim().isEmpty()) return "";
        String t = createTime.trim();
        if (t.length() != 19 || !t.matches("\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2}")) return "";
        return t;
    }

    /** 群列表（委托管理端读路径） */
    public List<Map<String, Object>> list(String keyword, String status) {
        return qqGroupService.list(keyword, status);
    }

    private Map<String, Object> findByGroupId(String groupId) {
        try {
            return jdbc.queryForObject("SELECT id, group_id FROM qq_groups WHERE group_id=?",
                    new RowMapMapper(), groupId);
        } catch (org.springframework.dao.EmptyResultDataAccessException e) {
            return null;
        }
    }
}
