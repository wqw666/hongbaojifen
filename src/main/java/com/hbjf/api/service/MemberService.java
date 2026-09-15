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
 * - 积分来源（source）：manual 后台手动 / approve 群内审批 / game 玩法结算 —— 「操作改分」与
 *   「游戏结算改分」分开记录，会员列表与流水按来源聚合统计；本服务的入口只写 manual/approve，
 *   game 由 GameRecordService 对局结算写入
 */
@Service
public class MemberService {

    /** 来源：后台手动上下分 */
    public static final String SOURCE_MANUAL = "manual";
    /** 来源：群内审批通过（agent 积分审批） */
    public static final String SOURCE_APPROVE = "approve";
    /** 来源：玩法结算 */
    public static final String SOURCE_GAME = "game";

    private static final DateTimeFormatter FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    private final JdbcTemplate jdbc;

    public MemberService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    // ========== 会员管理 ==========

    /** 列表（QQ/昵称模糊 + 状态过滤 + 来源群过滤）；每行附带按来源拆分的积分/流水合计 */
    public List<Map<String, Object>> list(String keyword, String status, String groupId) {
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
        if (groupId != null && !groupId.isEmpty()) {
            sql.append(" AND group_id = ?");
            args.add(groupId);
        }
        sql.append(" ORDER BY points DESC, id DESC");
        List<Map<String, Object>> rows = jdbc.query(sql.toString(), new RowMapMapper(), args.toArray());
        attachPointBreakdown(rows);
        return rows;
    }

    /**
     * 按「会员 × 来源」聚合积分与流水额，合并进会员列表行。
     * point_records 永久保留（不随保留策略清理）→ 实时聚合永远精确，故不落冗余计数列（避免计数漂移）。
     */
    private void attachPointBreakdown(List<Map<String, Object>> members) {
        if (members == null || members.isEmpty()) return;
        Map<String, Map<String, Object>> agg = new java.util.HashMap<>();
        for (Map<String, Object> r : jdbc.query(
                "SELECT member_id AS mid, COALESCE(source,'manual') AS src,"
                        + " COALESCE(SUM(CASE WHEN delta > 0 THEN delta END),0) AS inc,"
                        + " COALESCE(SUM(CASE WHEN delta < 0 THEN -delta END),0) AS outc,"
                        + " COALESCE(SUM(flow_amount),0) AS flow"
                        + " FROM point_records GROUP BY member_id, COALESCE(source,'manual')",
                new RowMapMapper())) {
            agg.put(str(r.get("mid")) + "|" + str(r.get("src")), r);
        }
        for (Map<String, Object> m : members) {
            String mid = str(m.get("id"));
            long[] manual = sumOf(agg, mid, SOURCE_MANUAL);
            long[] approve = sumOf(agg, mid, SOURCE_APPROVE);
            long[] game = sumOf(agg, mid, SOURCE_GAME);
            m.put("manual_income", manual[0]);
            m.put("manual_outcome", manual[1]);
            m.put("approve_income", approve[0]);
            m.put("approve_outcome", approve[1]);
            m.put("game_income", game[0]);
            m.put("game_outcome", game[1]);
            m.put("game_flow", game[2]);
        }
    }

    /** agg 取值：[上分, 下分, 流水额] */
    private long[] sumOf(Map<String, Map<String, Object>> agg, String memberId, String source) {
        Map<String, Object> r = agg.get(memberId + "|" + source);
        if (r == null) return new long[]{0, 0, 0};
        return new long[]{num(r.get("inc")), num(r.get("outc")), num(r.get("flow"))};
    }

    private static String str(Object v) {
        return v == null ? "" : String.valueOf(v);
    }

    private static long num(Object v) {
        return v == null ? 0 : ((Number) v).longValue();
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
        return adjustPoints(qq, delta, reason, operator, bizNo, SOURCE_MANUAL);
    }

    /**
     * 调整积分（带来源）
     * @param source 来源：manual 后台手动 / approve 群内审批（game 只能由对局结算写，这里会归一到 manual）
     */
    @Transactional
    public Map<String, Object> adjustPoints(String qq, long delta, String reason, String operator, String bizNo,
                                            String source) {
        if (qq == null || qq.trim().isEmpty()) throw new ApiException(ErrorCode.PARAM_INVALID, "QQ号不能为空");
        qq = qq.trim();
        if (delta == 0) throw new ApiException(ErrorCode.PARAM_INVALID, "积分变动不能为0");
        if (reason == null || reason.trim().isEmpty()) throw new ApiException(ErrorCode.PARAM_INVALID, "原因不能为空");
        String src = SOURCE_APPROVE.equals(source) ? SOURCE_APPROVE : SOURCE_MANUAL;

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
        jdbc.update("INSERT INTO point_records (qq, member_id, delta, flow_amount, type, reason, operator, biz_no, source, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                qq, Long.parseLong(memberId), delta, delta, type, reason, operator == null ? "" : operator,
                bizNo == null || bizNo.isEmpty() ? null : bizNo, src, now);

        return MapBuilder.of("qq", qq, "delta", delta, "points", current + delta,
                "bizNo", bizNo == null ? "" : bizNo, "source", src);
    }

    // ========== 积分流水 ==========

    /** 流水查询（QQ/类型/来源过滤 + 分页）；附该 QQ 的按来源合计（全量口径，不随筛选变化） */
    public Map<String, Object> records(String qq, String type, int page, int size) {
        return records(qq, type, null, page, size);
    }

    /** 流水查询（带来源过滤：manual 后台手动 / approve 群内审批 / game 玩法结算） */
    public Map<String, Object> records(String qq, String type, String source, int page, int size) {
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
        if (source != null && !source.isEmpty()) {
            where.append(" AND COALESCE(source,'manual') = ?");
            args.add(source);
        }
        int total = jdbc.queryForObject("SELECT COUNT(*) FROM point_records" + where, Integer.class, args.toArray());
        int offset = Math.max(0, (page - 1) * size);
        List<Map<String, Object>> rows = jdbc.query(
                "SELECT id, qq, delta, flow_amount, COALESCE(source,'manual') AS source, type, reason, operator, biz_no, created_at"
                        + " FROM point_records" + where +
                " ORDER BY id DESC LIMIT " + size + " OFFSET " + offset,
                new RowMapMapper(), args.toArray());
        return MapBuilder.of("total", total, "page", page, "size", size, "list", rows, "summary", summary(qq));
    }

    /** 按来源合计（会员流水抽屉的合计卡）：手动/审批各自的上分下分、玩法得分失分与玩法流水额 */
    private Map<String, Object> summary(String qq) {
        boolean byQq = qq != null && !qq.isEmpty();
        long manualIn = 0, manualOut = 0, approveIn = 0, approveOut = 0;
        long gameIn = 0, gameOut = 0, gameFlow = 0;
        for (Map<String, Object> r : jdbc.query(
                "SELECT COALESCE(source,'manual') AS src,"
                        + " COALESCE(SUM(CASE WHEN delta > 0 THEN delta END),0) AS inc,"
                        + " COALESCE(SUM(CASE WHEN delta < 0 THEN -delta END),0) AS outc,"
                        + " COALESCE(SUM(flow_amount),0) AS flow"
                        + " FROM point_records" + (byQq ? " WHERE qq = ?" : "")
                        + " GROUP BY COALESCE(source,'manual')",
                new RowMapMapper(), byQq ? new Object[]{qq} : new Object[]{})) {
            String src = str(r.get("src"));
            long inc = num(r.get("inc")), outc = num(r.get("outc")), flow = num(r.get("flow"));
            if (SOURCE_APPROVE.equals(src)) {
                approveIn = inc;
                approveOut = outc;
            } else if (SOURCE_GAME.equals(src)) {
                gameIn = inc;
                gameOut = outc;
                gameFlow = flow;
            } else {
                manualIn = inc;
                manualOut = outc;
            }
        }
        return MapBuilder.of(
                "manual_income", manualIn, "manual_outcome", manualOut,
                "approve_income", approveIn, "approve_outcome", approveOut,
                "game_income", gameIn, "game_outcome", gameOut, "game_flow", gameFlow);
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
