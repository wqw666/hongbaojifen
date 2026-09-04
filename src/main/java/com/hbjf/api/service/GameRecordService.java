package com.hbjf.api.service;

import com.hbjf.api.dao.RowMapMapper;
import com.hbjf.api.exception.ApiException;
import com.hbjf.api.exception.ErrorCode;
import com.hbjf.api.util.MapBuilder;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 游戏记录与结算服务
 * - agent 上报一局游戏（round）：玩法 + 群 + 事件时间线，服务端校验后直接入账
 * - round_id 唯一 → 重复上报返回 duplicate 标记，不重复入账（幂等）
 * - 入账前逐事件校验：玩家必须是已建档且启用的会员；下分需余额充足；
 *   不满足的玩家整条跳过并记入 warning（绝不把余额打成负数、绝不整局回滚其余玩家）
 * - 自动结算不受“禁止手动上下分”权限限制（那是给人手调的开关），但执行器被封禁/操作员停用时拒绝上报
 * - 入账通过直接 SQL 落账（member/point_records 同事务），不复用 MemberService.adjustPoints
 *   （本事务内逐事件 catch-and-continue 会触发 Spring rollback-only）
 */
@Service
public class GameRecordService {

    private static final DateTimeFormatter FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");
    private static final int MAX_EVENTS = 2000;
    private static final int MAX_WARNING_CHARS = 480;
    private static final int MAX_SAMPLE_WARNINGS = 6;

    private final JdbcTemplate jdbc;
    private final ExecutorService executorService;

    public GameRecordService(JdbcTemplate jdbc, ExecutorService executorService) {
        this.jdbc = jdbc;
        this.executorService = executorService;
    }

    // ========== 上报与入账 ==========

    /**
     * 上报一局游戏并结算入账
     * @param body {executor_token, round_id, play_id, play_name, group_id,
     *              events:[{qq, nickname, msg, reply, delta}]}
     */
    @Transactional
    public Map<String, Object> reportRound(Map<String, Object> body) {
        String roundId = str(body.get("round_id")).trim();
        if (roundId.isEmpty() || roundId.length() > 96) {
            throw new ApiException(ErrorCode.PARAM_INVALID, "round_id 必填且不超过96字符");
        }
        String token = str(body.get("executor_token")).trim();
        if (token.isEmpty()) {
            throw new ApiException(ErrorCode.PARAM_INVALID, "游戏结算必须携带 executor_token");
        }
        // 封禁/操作员停用 → 拒绝；自动结算不查 can_manual_points
        Map<String, Object> ex = executorService.requireOperable(token, "write");

        @SuppressWarnings("unchecked")
        List<Map<String, Object>> events = (List<Map<String, Object>>) body.get("events");
        if (events == null || events.isEmpty()) {
            throw new ApiException(ErrorCode.PARAM_INVALID, "events不能为空");
        }
        if (events.size() > MAX_EVENTS) {
            throw new ApiException(ErrorCode.PARAM_INVALID, "单局事件数超过上限 " + MAX_EVENTS);
        }

        // 幂等：round 已存在 → 直接返回，不重复入账
        Map<String, Object> existing = findRound(roundId);
        if (existing != null) {
            return MapBuilder.of("duplicate", true,
                    "round_id", roundId,
                    "member_count", existing.get("member_count"),
                    "total_delta", existing.get("total_delta"),
                    "event_count", existing.get("event_count"));
        }

        String playId = str(body.get("play_id"));
        if (!playId.isEmpty() && !playId.matches("\\d+")) {
            throw new ApiException(ErrorCode.PARAM_INVALID, "play_id 必须为数字");
        }
        String playName = str(body.get("play_name")).trim();
        String groupId = str(body.get("group_id")).trim();
        String now = LocalDateTime.now().format(FMT);
        String executorName = String.valueOf(ex.get("name"));
        String operatorQq = String.valueOf(ex.get("admin_qq"));

        // 先插局（UK round_id 兜底并发重复；重复则整体回滚）
        long recordId;
        try {
            jdbc.update("INSERT INTO game_records (round_id, play_id, play_name, group_id, executor_id, executor_name,"
                            + " operator_qq, member_count, total_delta, event_count, warning, created_at)"
                            + " VALUES (?,?,?,?,?,?,?,0,0,?,?,?)",
                    roundId, playId.isEmpty() ? 0 : Long.parseLong(playId),
                    playName.length() > 128 ? playName.substring(0, 128) : playName,
                    groupId.length() > 32 ? groupId.substring(0, 32) : groupId,
                    Long.parseLong(String.valueOf(ex.get("id"))),
                    executorName.length() > 64 ? executorName.substring(0, 64) : executorName,
                    operatorQq.length() > 32 ? operatorQq.substring(0, 32) : operatorQq,
                    events.size(), "", now);
            recordId = jdbc.queryForObject("SELECT LAST_INSERT_ID()", Long.class);
        } catch (DuplicateKeyException e) {
            return MapBuilder.of("duplicate", true, "round_id", roundId, "message", "该局已结算过");
        }

        // 逐事件校验并入账：非法结构（QQ/delta 不合法）无时间线；未知/停用/余额不足 → 该事件不入账
        // 但时间线照记（delta 记实际入账值 0，回放不误导；绝不把余额打成负数、不整局回滚其余玩家）
        List<String> warnings = new ArrayList<>();
        Set<String> settledQqs = new LinkedHashSet<>();
        long totalDelta = 0;
        int memberCount = 0;
        int evIndex = 0;

        for (Map<String, Object> ev : events) {
            evIndex++;
            String qq = str(ev.get("qq")).trim();
            if (!qq.matches("\\d{5,12}")) {
                addWarning(warnings, "非法QQ:" + qq + " 已跳过");
                continue;
            }
            String nickname = cut(str(ev.get("nickname")), 64);
            String msg = cut(str(ev.get("msg")), 512);
            String reply = cut(str(ev.get("reply")), 512);
            long delta;
            try {
                delta = ev.get("delta") == null ? 0 : Long.parseLong(String.valueOf(ev.get("delta")));
            } catch (NumberFormatException e) {
                addWarning(warnings, qq + " 积分delta非法 已跳过");
                continue;
            }

            // 玩家必须是已建档会员；未知与停用分开提示
            Map<String, Object> member = findMember(qq);
            if (member == null) {
                addWarning(warnings, "未知会员:" + qq + " 未入账");
            } else if (!"active".equals(member.get("status"))) {
                addWarning(warnings, "会员已停用:" + qq + " 未入账");
            }
            if (member == null || !"active".equals(member.get("status"))) {
                jdbc.update("INSERT INTO game_record_events (record_id, qq, nickname, msg, reply, delta, created_at)"
                                + " VALUES (?,?,?,?,?,?,?)",
                        recordId, qq, nickname, msg, reply, 0, now);
                continue;
            }

            long current = member.get("points") == null ? 0 : ((Number) member.get("points")).longValue();
            long applied = 0;
            // 下分余额校验：不足则该事件不入账（不出现负余额）
            if (current + delta < 0) {
                addWarning(warnings, qq + " 余额不足(当前" + current + ") 未入账");
            } else if (delta != 0) {
                long memberId = ((Number) member.get("id")).longValue();
                long abs = Math.abs(delta);
                jdbc.update("UPDATE members SET points=points+?, total_income=total_income+?,"
                                + " total_outcome=total_outcome+?, updated_at=? WHERE id=?",
                        delta, delta > 0 ? abs : 0, delta < 0 ? abs : 0, now, memberId);
                // biz_no = game:{round_id}:{qq}:{事件序号} —— 同一玩家一局内可有多次增减，各自留痕；
                // 整局幂等已由 game_records.round_id 唯一键保证（事件级不会真重复）
                jdbc.update("INSERT INTO point_records (qq, member_id, delta, type, reason, operator, biz_no, created_at)"
                                + " VALUES (?,?,?,?,?,?,?,?)",
                        qq, memberId, delta, delta > 0 ? "INCOME" : "OUTCOME",
                        "玩法:" + (playName.isEmpty() ? "游戏" : playName) + " 结算",
                        "executor:" + executorName,
                        "game:" + roundId + ":" + qq + ":" + evIndex, now);
                applied = delta;
                totalDelta += delta;
            }

            // 事件时间线（完整过程日志，供回放；delta 为实际入账值）
            jdbc.update("INSERT INTO game_record_events (record_id, qq, nickname, msg, reply, delta, created_at)"
                            + " VALUES (?,?,?,?,?,?,?)",
                    recordId, qq, nickname, msg, reply, applied, now);

            if (settledQqs.add(qq)) memberCount++;
        }

        String warningText = String.join("; ", warnings);
        if (warningText.length() > MAX_WARNING_CHARS) {
            warningText = warningText.substring(0, MAX_WARNING_CHARS);
        }
        jdbc.update("UPDATE game_records SET member_count=?, total_delta=?, warning=? WHERE id=?",
                memberCount, totalDelta, warningText, recordId);

        return MapBuilder.of("round_id", roundId, "duplicate", false,
                "member_count", memberCount, "total_delta", totalDelta,
                "event_count", events.size(), "warning_count", warnings.size(),
                "warning", warningText);
    }

    // ========== 查询（管理端回放） ==========

    /** 游戏记录分页列表（可过滤群号/玩法名） */
    public Map<String, Object> list(String groupId, String playName, int page, int size) {
        StringBuilder where = new StringBuilder(" WHERE 1=1");
        List<Object> args = new ArrayList<>();
        if (groupId != null && !groupId.isEmpty()) {
            where.append(" AND group_id = ?");
            args.add(groupId);
        }
        if (playName != null && !playName.isEmpty()) {
            where.append(" AND play_name LIKE ?");
            args.add("%" + playName + "%");
        }
        int total = jdbc.queryForObject("SELECT COUNT(*) FROM game_records" + where, Integer.class, args.toArray());
        int offset = Math.max(0, (page - 1) * size);
        List<Map<String, Object>> rows = jdbc.query(
                "SELECT id, round_id, play_id, play_name, group_id, executor_id, executor_name, operator_qq,"
                        + " member_count, total_delta, event_count, warning, created_at FROM game_records" + where
                        + " ORDER BY id DESC LIMIT " + size + " OFFSET " + offset,
                new RowMapMapper(), args.toArray());
        return MapBuilder.of("total", total, "page", page, "size", size, "list", rows);
    }

    /** 局详情（含事件时间线，供回放） */
    public Map<String, Object> detail(Long id) {
        Map<String, Object> record;
        try {
            record = jdbc.queryForObject(
                    "SELECT id, round_id, play_id, play_name, group_id, executor_id, executor_name, operator_qq,"
                            + " member_count, total_delta, event_count, warning, created_at FROM game_records WHERE id=?",
                    new RowMapMapper(), id);
        } catch (org.springframework.dao.EmptyResultDataAccessException e) {
            throw new ApiException(ErrorCode.NOT_FOUND, "该游戏记录不存在");
        }
        List<Map<String, Object>> events = jdbc.query(
                "SELECT id, qq, nickname, msg, reply, delta, created_at FROM game_record_events"
                        + " WHERE record_id=? ORDER BY id ASC",
                new RowMapMapper(), id);
        return MapBuilder.of("record", record, "events", events);
    }

    // ========== 内部 ==========

    private Map<String, Object> findRound(String roundId) {
        try {
            return jdbc.queryForObject("SELECT id, member_count, total_delta, event_count FROM game_records WHERE round_id=?",
                    new RowMapMapper(), roundId);
        } catch (org.springframework.dao.EmptyResultDataAccessException e) {
            return null;
        }
    }

    private Map<String, Object> findMember(String qq) {
        try {
            return jdbc.queryForObject("SELECT id, points, status FROM members WHERE qq=?",
                    new RowMapMapper(), qq);
        } catch (org.springframework.dao.EmptyResultDataAccessException e) {
            return null;
        }
    }

    private void addWarning(List<String> warnings, String text) {
        if (warnings.size() < MAX_SAMPLE_WARNINGS) warnings.add(text);
        else if (warnings.size() == MAX_SAMPLE_WARNINGS) warnings.add("...(更多已省略)");
    }

    private String cut(String s) {
        return s == null ? "" : s;
    }

    private String cut(String s, int len) {
        s = s == null ? "" : s;
        return s.length() > len ? s.substring(0, len) : s;
    }

    private String str(Object v) {
        return v == null ? "" : String.valueOf(v);
    }
}
