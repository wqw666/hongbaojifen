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
 * game_count/rake_total = 累计对局数/累计抽水（结算入账时累加；game_records 有 30 天保留期，累计值必须落库）
 */
@Service
public class QqGroupService {

    private static final DateTimeFormatter FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");
    /** 近 N 天窗口（与 game_records 默认 30 天保留期一致，故保留期内是精确值） */
    private static final int RECENT_DAYS = 30;

    private final JdbcTemplate jdbc;

    public QqGroupService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    /** 列表（群号/群名模糊 + 状态过滤；附带累计统计 / 近30天统计 / 群内会员积分合计） */
    public List<Map<String, Object>> list(String keyword, String status) {
        StringBuilder sql = new StringBuilder(
                "SELECT id, group_id, group_name, create_time, owner_qq, admin_qqs, member_count, status,"
                        + " note, ban_reason, executor_id, game_count, rake_total, created_at, updated_at"
                        + " FROM qq_groups WHERE 1=1");
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
        return enrich(jdbc.query(sql.toString(), new RowMapMapper(), args.toArray()));
    }

    /**
     * 给群列表补三个展示字段（管理端与 agent 开放接口共用同一读路径）：
     * - points_total：群内会员当前积分合计（members.group_id = 来源群）
     * - game_count_30d / rake_total_30d：近 30 天对局数与抽水（实时聚合；累计值直接用 qq_groups 计数列，
     *   因为 game_records 会被保留策略清理，累计值不能靠实时聚合）
     */
    private List<Map<String, Object>> enrich(List<Map<String, Object>> groups) {
        if (groups.isEmpty()) return groups;
        String since = LocalDateTime.now().minusDays(RECENT_DAYS).format(FMT);
        Map<String, long[]> recent = new java.util.HashMap<>();
        for (Map<String, Object> r : jdbc.query(
                "SELECT group_id AS gid, COUNT(*) AS c, COALESCE(SUM(-total_delta),0) AS fee"
                        + " FROM game_records WHERE group_id <> '' AND created_at >= ? GROUP BY group_id",
                new RowMapMapper(), since)) {
            recent.put(String.valueOf(r.get("gid")), new long[]{
                    ((Number) r.get("c")).longValue(), ((Number) r.get("fee")).longValue()});
        }
        Map<String, Long> points = new java.util.HashMap<>();
        for (Map<String, Object> r : jdbc.query(
                "SELECT group_id AS gid, COALESCE(SUM(points),0) AS pts FROM members"
                        + " WHERE group_id <> '' GROUP BY group_id",
                new RowMapMapper())) {
            points.put(String.valueOf(r.get("gid")), ((Number) r.get("pts")).longValue());
        }
        for (Map<String, Object> g : groups) {
            String gid = String.valueOf(g.get("group_id"));
            long[] r = recent.get(gid);
            g.put("game_count_30d", r == null ? 0L : r[0]);
            g.put("rake_total_30d", r == null ? 0L : r[1]);
            g.put("points_total", points.getOrDefault(gid, 0L));
        }
        return groups;
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
