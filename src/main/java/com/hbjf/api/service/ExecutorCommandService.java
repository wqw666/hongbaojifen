package com.hbjf.api.service;

import com.hbjf.api.dao.RowMapMapper;
import com.hbjf.api.exception.ApiException;
import com.hbjf.api.exception.ErrorCode;
import com.hbjf.api.util.MapBuilder;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;

/**
 * 执行器命令通道 — 总后台远程管理 agent
 *
 * 状态机：pending(待下发) → sent(已下发待回报) → done/failed
 * - poll 原子取走 pending 并标记 sent（带 status='pending' 条件防并发重复取走）
 * - sent 超过 5 分钟未回报：下次 poll 自动重排回 pending（VARCHAR 字典序 = 时间序）
 * - 命令必须幂等（v1 命令集：set_config / sync_members / get_status），
 *   重投递最多重复执行一次，不产生副作用
 */
@Service
public class ExecutorCommandService {

    private static final DateTimeFormatter FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");
    private static final long SENT_TIMEOUT_MINUTES = 5;
    private static final long CLEANUP_DAYS = 7;
    private static final int POLL_BATCH = 20;
    private static final int PENDING_CAP = 50;
    private static final int LIST_LIMIT = 200;

    private final JdbcTemplate jdbc;
    private final ExecutorService executorService;

    public ExecutorCommandService(JdbcTemplate jdbc, ExecutorService executorService) {
        this.jdbc = jdbc;
        this.executorService = executorService;
    }

    // ========== 管理端：下发/查询 ==========

    /** 下发命令（同 command+params 且未完成时去重；pending+sent ≥ 50 拒绝；顺手清理 7 天前的历史） */
    public Map<String, Object> dispatch(Long executorId, String command, String params) {
        if (command == null || command.trim().isEmpty()) throw new ApiException(ErrorCode.PARAM_INVALID, "命令不能为空");
        if (executorId == null) throw new ApiException(ErrorCode.PARAM_INVALID, "执行器id不能为空");
        String paramsStr = params == null ? "" : params.trim();

        Integer cnt = jdbc.queryForObject("SELECT COUNT(*) FROM executors WHERE id=?", Integer.class, executorId);
        if (cnt == null || cnt == 0) throw new ApiException(ErrorCode.NOT_FOUND, "执行器不存在");

        List<Map<String, Object>> dup = jdbc.query(
                "SELECT id, command, params, status, created_at FROM executor_commands WHERE executor_id=? AND command=? AND params=? AND status IN ('pending','sent') ORDER BY id LIMIT 1",
                new RowMapMapper(), executorId, command.trim(), paramsStr);
        if (!dup.isEmpty()) {
            Map<String, Object> row = dup.get(0);
            return MapBuilder.of("id", row.get("id"), "command", row.get("command"), "params", row.get("params"),
                    "status", row.get("status"), "created_at", row.get("created_at"),
                    "message", "已存在相同待执行命令");
        }

        Integer pending = jdbc.queryForObject(
                "SELECT COUNT(*) FROM executor_commands WHERE executor_id=? AND status IN ('pending','sent')",
                Integer.class, executorId);
        if (pending != null && pending >= PENDING_CAP) {
            throw new ApiException(ErrorCode.PARAM_INVALID, "待执行命令过多（≥" + PENDING_CAP + "），请先处理");
        }

        // 清理 7 天前已完成的命令，防止表膨胀
        String cutoff = LocalDateTime.now().minusDays(CLEANUP_DAYS).format(FMT);
        jdbc.update("DELETE FROM executor_commands WHERE executor_id=? AND status IN ('done','failed') AND created_at < ?",
                executorId, cutoff);

        String now = LocalDateTime.now().format(FMT);
        org.springframework.jdbc.support.GeneratedKeyHolder keyHolder = new org.springframework.jdbc.support.GeneratedKeyHolder();
        jdbc.update(con -> {
            var ps = con.prepareStatement(
                    "INSERT INTO executor_commands (executor_id, command, params, status, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                    java.sql.Statement.RETURN_GENERATED_KEYS);
            ps.setLong(1, executorId);
            ps.setString(2, command.trim());
            ps.setString(3, paramsStr);
            ps.setString(4, "pending");
            ps.setString(5, now);
            ps.setString(6, now);
            return ps;
        }, keyHolder);
        Number id = keyHolder.getKey();
        return MapBuilder.of("id", id == null ? 0 : id.longValue(),
                "command", command.trim(), "params", paramsStr, "status", "pending", "created_at", now);
    }

    /** 命令列表（管理端查看，status 可过滤） */
    public List<Map<String, Object>> listByExecutor(Long executorId, String status) {
        if (status != null && !status.isEmpty()) {
            return jdbc.query(
                    "SELECT id, command, params, status, result, dispatched_at, created_at, updated_at FROM executor_commands WHERE executor_id=? AND status=? ORDER BY id DESC LIMIT " + LIST_LIMIT,
                    new RowMapMapper(), executorId, status);
        }
        return jdbc.query(
                "SELECT id, command, params, status, result, dispatched_at, created_at, updated_at FROM executor_commands WHERE executor_id=? ORDER BY id DESC LIMIT " + LIST_LIMIT,
                new RowMapMapper(), executorId);
    }

    // ========== 开放端：poll / 回报 ==========

    /** agent 拉取待执行命令（token 校验+封禁守卫；sent 超时重排 + 原子取走标记） */
    public List<Map<String, Object>> poll(String token) {
        Map<String, Object> exe = executorService.resolveByToken(token);

        // 1) 重排：sent 超过 5 分钟未回报 → 回 pending 重新投递
        String staleBefore = LocalDateTime.now().minusMinutes(SENT_TIMEOUT_MINUTES).format(FMT);
        jdbc.update("UPDATE executor_commands SET status='pending', dispatched_at='', updated_at=? WHERE executor_id=? AND status='sent' AND dispatched_at<>'' AND dispatched_at < ?",
                LocalDateTime.now().format(FMT), exe.get("id"), staleBefore);

        // 2) 取走 pending 并原子标记 sent
        List<Map<String, Object>> rows = jdbc.query(
                "SELECT id, command, params, created_at FROM executor_commands WHERE executor_id=? AND status='pending' ORDER BY id LIMIT " + POLL_BATCH,
                new RowMapMapper(), exe.get("id"));
        if (!rows.isEmpty()) {
            String now = LocalDateTime.now().format(FMT);
            String in = String.join(",", Collections.nCopies(rows.size(), "?"));
            List<Object> args = new ArrayList<>();
            args.add(now); // dispatched_at
            args.add(now); // updated_at
            for (Map<String, Object> r : rows) args.add(r.get("id"));
            args.add(exe.get("id"));
            jdbc.update("UPDATE executor_commands SET status='sent', dispatched_at=?, updated_at=? WHERE id IN (" + in + ") AND executor_id=? AND status='pending'",
                    args.toArray());
        }
        return rows;
    }

    /** agent 回报命令执行结果（仅能回报自己的命令；封禁中被拒） */
    public void reportResult(String token, Long commandId, String status, String message) {
        Map<String, Object> exe = executorService.resolveByToken(token);
        if (commandId == null) throw new ApiException(ErrorCode.PARAM_INVALID, "命令id不能为空");
        if (!"done".equals(status) && !"failed".equals(status)) {
            throw new ApiException(ErrorCode.PARAM_INVALID, "status 只能是 done/failed");
        }
        String now = LocalDateTime.now().format(FMT);
        int rows = jdbc.update("UPDATE executor_commands SET status=?, result=?, updated_at=? WHERE id=? AND executor_id=?",
                status, message == null ? "" : message, now, commandId, exe.get("id"));
        if (rows == 0) throw new ApiException(ErrorCode.NOT_FOUND, "命令不存在或不属于该执行器");
    }
}
