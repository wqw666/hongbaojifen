package com.hbjf.api.service;

import com.hbjf.api.dao.RowMapMapper;
import com.hbjf.api.exception.ApiException;
import com.hbjf.api.exception.ErrorCode;
import com.hbjf.api.util.MapBuilder;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * 执行器管理 — 部署在各游戏群机器上的 agent 程序
 * - 每个执行器注册后获得唯一 token，通过心跳维持在线；一个执行器管理多个群（见 qq_groups.executor_id）
 * - 封禁：banned_at 非空即封禁，全部带 token 的写操作被拒；可「仅封禁」或「封禁并重置token」
 * - 心跳负载：host/version/admin_qq（登录的管理员QQ）/来源IP（服务端记录，不可伪造）
 * - resolveByToken / requireOperable 是各 open 接口的公共鉴权守卫
 */
@Service
public class ExecutorService {

    private static final DateTimeFormatter FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    private final JdbcTemplate jdbc;
    /** 心跳超时秒数：超过该值未心跳的在线执行器自动置为离线（封禁中除外） */
    private final int offlineAfterSeconds;

    public ExecutorService(JdbcTemplate jdbc,
                           @Value("${app.executor-offline-seconds:30}") int offlineAfterSeconds) {
        this.jdbc = jdbc;
        this.offlineAfterSeconds = offlineAfterSeconds;
    }

    // ========== 管理端 ==========

    /** 心跳超时自动离线：超过 offlineAfterSeconds 未心跳的在线执行器置为 offline（封禁中保持封禁态） */
    private void markStaleOffline() {
        String now = LocalDateTime.now().format(FMT);
        String cutoff = LocalDateTime.now().minusSeconds(offlineAfterSeconds).format(FMT);
        jdbc.update("UPDATE executors SET status='offline', updated_at=? WHERE status='online' AND banned_at=''"
                + " AND last_heartbeat <> '' AND last_heartbeat < ?", now, cutoff);
    }

    /** 列表（keyword 模糊 name/host/admin_qq；status=banned 视为封禁筛选，其余按状态） */
    public List<Map<String, Object>> list(String keyword, String status) {
        markStaleOffline();
        StringBuilder sql = new StringBuilder(
                "SELECT e.id, e.name, e.token, e.status, e.version, e.host, e.admin_qq, e.last_ip, e.last_heartbeat,"
                        + " e.game_fee_rate, e.banned_at, e.ban_reason, e.note, e.created_at, e.updated_at,"
                        + " (SELECT q.nickname FROM qq_accounts q WHERE q.qq = e.admin_qq AND q.type = 'admin_qq') AS admin_nickname"
                        + " FROM executors e WHERE 1=1");
        List<Object> args = new java.util.ArrayList<>();
        if (keyword != null && !keyword.isEmpty()) {
            sql.append(" AND (e.name LIKE ? OR e.host LIKE ? OR e.admin_qq LIKE ?)");
            args.add("%" + keyword + "%");
            args.add("%" + keyword + "%");
            args.add("%" + keyword + "%");
        }
        if ("banned".equals(status)) {
            sql.append(" AND e.banned_at <> ''");
        } else if (status != null && !status.isEmpty()) {
            sql.append(" AND e.status = ? AND e.banned_at = ''");
            args.add(status);
        }
        sql.append(" ORDER BY e.id DESC");
        List<Map<String, Object>> rows = jdbc.query(sql.toString(), new RowMapMapper(), args.toArray());
        // 附带每个执行器当前管理的群（1 执行器 : N 群）
        for (Map<String, Object> row : rows) {
            List<Map<String, Object>> groups = jdbc.query(
                    "SELECT group_id, group_name, status, member_count FROM qq_groups WHERE executor_id=? ORDER BY id",
                    new RowMapMapper(), row.get("id"));
            row.put("groups", groups);
            row.put("group_count", groups.size());
        }
        return rows;
    }

    /** 新增执行器（自动生成 token；无“负责群”，群由 agent 上报时绑定） */
    public Map<String, Object> create(String name, String version, String host) {
        if (name == null || name.trim().isEmpty()) throw new ApiException(ErrorCode.PARAM_INVALID, "执行器名称不能为空");
        String token = UUID.randomUUID().toString().replace("-", "");
        String now = LocalDateTime.now().format(FMT);
        try {
            jdbc.update("INSERT INTO executors (name, token, status, version, host, created_at) VALUES (?,?,?,?,?,?)",
                    name.trim(), token, "offline", version == null ? "" : version,
                    host == null ? "" : host, now);
        } catch (org.springframework.dao.DuplicateKeyException e) {
            throw new ApiException(ErrorCode.PARAM_INVALID, "执行器名称已存在: " + name.trim());
        }
        Map<String, Object> row = jdbc.queryForObject(
                "SELECT id, name, token, status, version, host, created_at FROM executors WHERE name=?",
                new RowMapMapper(), name.trim());
        row.put("token_plain", token); // 只显示一次，token 不落明文库外
        return row;
    }

    /** 更新基本信息（名称/版本；状态由心跳与封禁驱动，不允许手工伪造在线） */
    public void update(Long id, String name, String version) {
        if (name == null || name.trim().isEmpty()) throw new ApiException(ErrorCode.PARAM_INVALID, "执行器名称不能为空");
        String now = LocalDateTime.now().format(FMT);
        try {
            jdbc.update("UPDATE executors SET name=?, version=?, updated_at=? WHERE id=?",
                    name.trim(), version == null ? "" : version, now, id);
        } catch (org.springframework.dao.DuplicateKeyException e) {
            throw new ApiException(ErrorCode.PARAM_INVALID, "执行器名称已存在: " + name.trim());
        }
    }

    /** 删除（其名下群解绑；命令历史由 FK 级联清理） */
    public void delete(Long id) {
        jdbc.update("UPDATE qq_groups SET executor_id=0 WHERE executor_id=?", id);
        jdbc.update("DELETE FROM executors WHERE id=?", id);
    }

    /** 重置 token */
    public String resetToken(Long id) {
        String token = UUID.randomUUID().toString().replace("-", "");
        jdbc.update("UPDATE executors SET token=? WHERE id=?", token, id);
        return token;
    }

    /** 封禁（banned_at 非空即封禁）。resetToken=true 时一并重置，旧 token 立即作废 */
    public Map<String, Object> ban(Long id, String reason, boolean resetToken) {
        Integer c = jdbc.queryForObject("SELECT COUNT(*) FROM executors WHERE id=?", Integer.class, id);
        if (c == null || c == 0) throw new ApiException(ErrorCode.NOT_FOUND, "执行器不存在");
        String now = LocalDateTime.now().format(FMT);
        String newToken = resetToken ? UUID.randomUUID().toString().replace("-", "") : null;
        jdbc.update("UPDATE executors SET banned_at=?, ban_reason=?, token=COALESCE(?, token), updated_at=? WHERE id=?",
                now, reason == null ? "" : reason, newToken, now, id);
        return MapBuilder.of("banned_at", now, "token", newToken == null ? "" : newToken);
    }

    /** 解封（token 不变；若封禁时已重置 token，解封后需重新配置 agent） */
    public void unban(Long id) {
        int rows = jdbc.update("UPDATE executors SET banned_at='', ban_reason='', updated_at=? WHERE id=?",
                LocalDateTime.now().format(FMT), id);
        if (rows == 0) throw new ApiException(ErrorCode.NOT_FOUND, "执行器不存在");
        markStaleOffline(); // 解封后若早已超时未心跳，立即回到离线而非沿用封禁前的 online
    }

    // ========== 心跳（agent 调用） ==========

    /**
     * 心跳注册/续活。负载：host/version/admin_qq/admin_nickname（登录的管理员QQ）；ip 由服务端记录。
     * 封禁中的执行器心跳被拒（agent 收到 40310 后停摆展示封禁态）
     */
    public Map<String, Object> heartbeat(String token, String host, String version,
                                         String adminQq, String adminNickname, String ip) {
        return heartbeat(token, host, version, adminQq, adminNickname, ip, null);
    }

    /** 心跳（可携带 game_fee_rate 千分比更新游戏费率，20=2%；null=不更新） */
    public Map<String, Object> heartbeat(String token, String host, String version,
                                         String adminQq, String adminNickname, String ip,
                                         Integer gameFeeRate) {
        Map<String, Object> exe = resolveByToken(token);
        String now = LocalDateTime.now().format(FMT);
        String admin = adminQq == null ? "" : adminQq.trim();
        markStaleOffline(); // 先刷掉所有超时执行器，再把本次心跳的置 online
        jdbc.update("UPDATE executors SET status='online', host=?, version=?,"
                        + " admin_qq=COALESCE(NULLIF(?,''), admin_qq), last_ip=?, last_heartbeat=?, updated_at=? WHERE id=?",
                host == null ? "" : host, version == null ? "" : version, admin, ip == null ? "" : ip, now, now,
                exe.get("id"));
        if (gameFeeRate != null && gameFeeRate >= 0 && gameFeeRate <= 1000) {
            jdbc.update("UPDATE executors SET game_fee_rate=?, updated_at=? WHERE id=?",
                    gameFeeRate, now, exe.get("id"));
        }
        if (!admin.isEmpty()) {
            upsertOperator(admin, adminNickname, ip == null ? "" : ip, host == null ? "" : host);
        }
        return MapBuilder.of("name", exe.get("name"), "server_time", now, "online", true,
                "admin_qq", admin.isEmpty() ? exe.get("admin_qq") : admin);
    }

    /** agent 登录的管理员QQ → qq_accounts 操作员行（幂等；自动建行，权限/状态由总后台管理） */
    private void upsertOperator(String qq, String nickname, String ip, String host) {
        String now = LocalDateTime.now().format(FMT);
        Integer exists = jdbc.queryForObject("SELECT COUNT(*) FROM qq_accounts WHERE qq=?", Integer.class, qq);
        if (exists == null || exists == 0) {
            try {
                jdbc.update("INSERT INTO qq_accounts (qq, nickname, type, status, remark, can_manual_points,"
                                + " last_login_at, last_login_ip, last_host, created_at, updated_at)"
                                + " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        qq, nickname == null ? "" : nickname, "admin_qq", "active", "agent 自动注册", "allowed",
                        now, ip, host, now, now);
                return;
            } catch (org.springframework.dao.DuplicateKeyException ignored) {
                // 并发自注册兜底：走更新
            }
        }
        jdbc.update("UPDATE qq_accounts SET type='admin_qq', nickname=COALESCE(NULLIF(?,''), nickname),"
                        + " last_login_at=?, last_login_ip=COALESCE(NULLIF(?,''), last_login_ip),"
                        + " last_host=COALESCE(NULLIF(?,''), last_host), updated_at=? WHERE qq=?",
                nickname == null ? "" : nickname, now, ip, host, now, qq);
    }

    // ========== 公共鉴权守卫（open 接口共用） ==========

    /**
     * token → 执行器（含封禁校验）。封禁中抛 40310；查无此 token 抛 404。
     * 心跳/命令 poll/回报等“仅鉴权”的调用用它
     */
    public Map<String, Object> resolveByToken(String token) {
        if (token == null || token.isEmpty()) throw new ApiException(ErrorCode.NOT_LOGGED_IN, "token不能为空");
        Map<String, Object> exe;
        try {
            exe = jdbc.queryForObject(
                    "SELECT id, name, status, banned_at, ban_reason, admin_qq FROM executors WHERE token=?",
                    new RowMapMapper(), token);
        } catch (org.springframework.dao.EmptyResultDataAccessException e) {
            throw new ApiException(ErrorCode.NOT_FOUND, "执行器不存在或token已重置");
        }
        String bannedAt = exe.get("banned_at") == null ? "" : String.valueOf(exe.get("banned_at"));
        if (!bannedAt.isEmpty()) {
            String reason = exe.get("ban_reason") == null ? "" : String.valueOf(exe.get("ban_reason"));
            throw new ApiException(ErrorCode.EXECUTOR_BANNED,
                    "执行器已被封禁" + (reason.isEmpty() ? "" : "：" + reason));
        }
        return exe;
    }

    /**
     * 执行器 + 操作员(管理员QQ)双重校验的写操作守卫。
     * @param purpose "write"=普通写操作（注册/群上报/游戏上报）；"manual"=手动上/下分（额外要求权限 allowed）
     * 返回含 executor id/name 与 admin_qq 的 map，供调用方记注册人/操作员
     */
    public Map<String, Object> requireOperable(String token, String purpose) {
        Map<String, Object> exe = resolveByToken(token);
        String adminQq = exe.get("admin_qq") == null ? "" : String.valueOf(exe.get("admin_qq"));
        if (adminQq.isEmpty()) {
            throw new ApiException(ErrorCode.PARAM_INVALID, "执行器尚未上报管理员QQ（请先完成一次心跳）");
        }
        Map<String, Object> op;
        try {
            op = jdbc.queryForObject(
                    "SELECT status, can_manual_points, nickname FROM qq_accounts WHERE qq=? AND type='admin_qq'",
                    new RowMapMapper(), adminQq);
        } catch (org.springframework.dao.EmptyResultDataAccessException e) {
            throw new ApiException(ErrorCode.NOT_FOUND, "操作员QQ未注册: " + adminQq);
        }
        if (!"active".equals(op.get("status"))) {
            throw new ApiException(ErrorCode.OPERATOR_DISABLED, "操作员QQ已被停用: " + adminQq);
        }
        if ("manual".equals(purpose) && !"allowed".equals(op.get("can_manual_points"))) {
            throw new ApiException(ErrorCode.PERMISSION_DENIED, "该操作员无手动上/下分权限（总后台可授权）");
        }
        return MapBuilder.of("id", exe.get("id"), "name", exe.get("name"),
                "admin_qq", adminQq, "can_manual_points", op.get("can_manual_points"));
    }
}
