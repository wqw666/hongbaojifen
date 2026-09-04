package com.hbjf.api.service;

import com.hbjf.api.dao.RowMapMapper;
import com.hbjf.api.exception.ApiException;
import com.hbjf.api.exception.ErrorCode;
import com.hbjf.api.util.MapBuilder;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.List;
import java.util.Map;

/**
 * 会员与积分核心服务
 * - 会员以 QQ 号为唯一标识；上分/下分写积分流水（事务）
 * - bizNo 幂等：同一业务单号重复提交不会重复加减
 * - 上分时会员不存在自动建档；下分时不存在直接报错
 */
@Service
public class MemberService {

    private static final DateTimeFormatter FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    private final JdbcTemplate jdbc;

    public MemberService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    // ========== 会员管理 ==========

    /** 列表（QQ/昵称模糊 + 状态过滤） */
    public List<Map<String, Object>> list(String keyword, String status) {
        StringBuilder sql = new StringBuilder(
                "SELECT id, qq, nickname, points, total_income, total_outcome, group_id, status, note, registrar_qq, created_at, updated_at FROM members WHERE 1=1");
        List<Object> args = new java.util.ArrayList<>();
        if (keyword != null && !keyword.isEmpty()) {
            sql.append(" AND (qq LIKE ? OR nickname LIKE ?)");
            args.add("%" + keyword + "%");
            args.add("%" + keyword + "%");
        }
        if (status != null && !status.isEmpty()) {
            sql.append(" AND status = ?");
            args.add(status);
        }
        sql.append(" ORDER BY points DESC, id DESC");
        return jdbc.query(sql.toString(), new RowMapMapper(), args.toArray());
    }

    /** 查单个会员（按 QQ） */
    public Map<String, Object> findByQq(String qq) {
        return findOne(" WHERE qq=?", qq);
    }

    /** 查单个会员（按主键） */
    public Map<String, Object> findById(Long id) {
        return findOne(" WHERE id=?", id);
    }

    private Map<String, Object> findOne(String whereSql, Object arg) {
        try {
            return jdbc.queryForObject(
                    "SELECT id, qq, nickname, points, total_income, total_outcome, group_id, status, note, registrar_qq, created_at, updated_at FROM members"
                            + whereSql,
                    new RowMapMapper(), arg);
        } catch (org.springframework.dao.EmptyResultDataAccessException e) {
            return null;
        }
    }

    /** 新增会员（手动建档） */
    public void create(String qq, String nickname, String groupId, String note) {
        if (qq == null || qq.trim().isEmpty()) throw new ApiException(ErrorCode.PARAM_INVALID, "QQ号不能为空");
        if (!qq.matches("\\d{5,12}")) throw new ApiException(ErrorCode.PARAM_INVALID, "QQ号格式不正确（5-12位数字）");
        String now = LocalDateTime.now().format(FMT);
        jdbc.update("INSERT INTO members (qq, nickname, group_id, note, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                qq.trim(), nickname == null ? "" : nickname, groupId == null ? "" : groupId,
                note == null ? "" : note, now, now);
    }

    /** 更新会员（昵称/来源群/备注/状态） */
    public void update(Long id, String nickname, String groupId, String note, String status) {
        String now = LocalDateTime.now().format(FMT);
        jdbc.update("UPDATE members SET nickname=?, group_id=?, note=?, status=?, updated_at=? WHERE id=?",
                nickname == null ? "" : nickname, groupId == null ? "" : groupId,
                note == null ? "" : note, status == null ? "active" : status, now, id);
    }

    /** 删除会员（有积分流水则拒绝，改为停用） */
    public void delete(Long id) {
        Integer c = jdbc.queryForObject("SELECT COUNT(*) FROM point_records WHERE member_id=?", Integer.class, id);
        if (c != null && c > 0) {
            throw new ApiException(ErrorCode.PARAM_INVALID, "该会员已有积分流水，不能删除，可改为停用");
        }
        jdbc.update("DELETE FROM members WHERE id=?", id);
    }

    // ========== 积分加减（核心） ==========

    /**
     * 调整积分：delta>0 上分，delta<0 下分
     * @param qq      会员QQ号（上分时不存在自动建档）
     * @param delta   积分变动（正/负）
     * @param reason  原因（必填）
     * @param operator 操作人（admin / 执行器名 / open）
     * @param bizNo   业务单号（幂等，可空）
     */
    @Transactional
    public Map<String, Object> adjustPoints(String qq, long delta, String reason, String operator, String bizNo) {
        if (qq == null || qq.trim().isEmpty()) throw new ApiException(ErrorCode.PARAM_INVALID, "QQ号不能为空");
        qq = qq.trim();
        if (delta == 0) throw new ApiException(ErrorCode.PARAM_INVALID, "积分变动不能为0");
        if (reason == null || reason.trim().isEmpty()) throw new ApiException(ErrorCode.PARAM_INVALID, "原因不能为空");

        // 幂等：bizNo 已存在则直接返回已处理结果，不重复加减
        if (bizNo != null && !bizNo.isEmpty()) {
            Integer c = jdbc.queryForObject("SELECT COUNT(*) FROM point_records WHERE biz_no=?", Integer.class, bizNo);
            if (c != null && c > 0) {
                return MapBuilder.of("duplicate", true, "qq", qq, "delta", delta);
            }
        }

        // 查会员：上分不存在自动建档；下分不存在报错
        Map<String, Object> m = findByQq(qq);
        String memberId;
        long current;
        if (m == null) {
            if (delta < 0) throw new ApiException(ErrorCode.NOT_FOUND, "会员不存在，无法下分（QQ号未建档）");
            create(qq, "", "", "");
            m = findByQq(qq);
        }
        memberId = String.valueOf(m.get("id"));
        current = m.get("points") == null ? 0 : ((Number) m.get("points")).longValue();

        // 余额不足拒绝
        if (current + delta < 0) {
            throw new ApiException(ErrorCode.BALANCE_NOT_ENOUGH, "积分不足，当前积分 " + current);
        }

        long abs = Math.abs(delta);
        String type = delta > 0 ? "INCOME" : "OUTCOME";
        String now = LocalDateTime.now().format(FMT);

        jdbc.update("UPDATE members SET points=points+?, total_income=total_income+?, total_outcome=total_outcome+?, updated_at=? WHERE id=?",
                delta, delta > 0 ? abs : 0, delta < 0 ? abs : 0, now, memberId);
        jdbc.update("INSERT INTO point_records (qq, member_id, delta, type, reason, operator, biz_no, created_at) VALUES (?,?,?,?,?,?,?,?)",
                qq, Long.parseLong(memberId), delta, type, reason, operator == null ? "" : operator,
                bizNo == null || bizNo.isEmpty() ? null : bizNo, now);

        return MapBuilder.of("qq", qq, "delta", delta, "points", current + delta, "bizNo", bizNo == null ? "" : bizNo);
    }

    // ========== 积分流水 ==========

    /** 流水查询（QQ/类型过滤 + 分页） */
    public Map<String, Object> records(String qq, String type, int page, int size) {
        StringBuilder where = new StringBuilder(" WHERE 1=1");
        List<Object> args = new java.util.ArrayList<>();
        if (qq != null && !qq.isEmpty()) {
            where.append(" AND qq = ?");
            args.add(qq);
        }
        if (type != null && !type.isEmpty()) {
            where.append(" AND type = ?");
            args.add(type);
        }
        int total = jdbc.queryForObject("SELECT COUNT(*) FROM point_records" + where, Integer.class, args.toArray());
        int offset = Math.max(0, (page - 1) * size);
        List<Map<String, Object>> rows = jdbc.query(
                "SELECT id, qq, delta, type, reason, operator, biz_no, created_at FROM point_records" + where +
                " ORDER BY id DESC LIMIT " + size + " OFFSET " + offset,
                new RowMapMapper(), args.toArray());
        return MapBuilder.of("total", total, "page", page, "size", size, "list", rows);
    }

    /** 统计概览 */
    public Map<String, Object> stats() {
        Integer memberCount = jdbc.queryForObject("SELECT COUNT(*) FROM members", Integer.class);
        Long totalPoints = jdbc.queryForObject("SELECT COALESCE(SUM(points),0) FROM members", Long.class);
        Integer incomeCount = jdbc.queryForObject("SELECT COUNT(*) FROM point_records WHERE delta > 0", Integer.class);
        Integer outcomeCount = jdbc.queryForObject("SELECT COUNT(*) FROM point_records WHERE delta < 0", Integer.class);
        return MapBuilder.of("member_count", memberCount, "total_points", totalPoints,
                "income_count", incomeCount, "outcome_count", outcomeCount);
    }
}
