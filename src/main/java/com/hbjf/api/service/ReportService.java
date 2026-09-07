package com.hbjf.api.service;

import com.hbjf.api.dao.RowMapMapper;
import com.hbjf.api.util.MapBuilder;
import org.springframework.core.env.Environment;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import javax.sql.DataSource;
import java.net.InetAddress;
import java.net.InterfaceAddress;
import java.net.NetworkInterface;
import java.sql.Connection;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Enumeration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 报表展示（只读总览，供非技术人员查看经营数据）
 *
 * 口径约定（页面展示必须与之一致）：
 * - 「手动 vs 玩法」分流：reason LIKE '玩法:%' = 玩法结算流水（agent 恒写「玩法:{玩法名} 结算」），
 *   其余 = 手动资金流水（充值上分 / 提现下分）。point_records 永久保留，流水统计永久准确。
 * - 「今日」= created_at ≥ 今天 00:00:00。
 * - 抽水（房费）= SUM(−game_records.total_delta)；每局守恒：total_delta 恒为 −抽水。
 *   局数/抽水/人次来自 game_records，受保留期限制（配置键 game_record_retention_days，默认 30 天），
 *   「近 N 天」cutoff 与 RetentionCleanupService 同一口径。
 * - 人次 = SUM(member_count)（每局实际结算会员数快照）；今日玩家数 = 当日对局事件去重 QQ
 *   （含被跳过未入账的尝试玩家，与单局明细列一致）。
 */
@Service
public class ReportService {

    private static final DateTimeFormatter FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");
    private static final String GAME_REASON_LIKE = "玩法:%";   // LIKE 匹配玩法结算流水
    private static final int RANK_LIMIT = 10;

    private final JdbcTemplate jdbc;
    private final DictService dictService;
    private final DataSource dataSource;
    private final Environment env;

    public ReportService(JdbcTemplate jdbc, DictService dictService, DataSource dataSource, Environment env) {
        this.jdbc = jdbc;
        this.dictService = dictService;
        this.dataSource = dataSource;
        this.env = env;
    }

    /** 报表总览：一次返回页面全部数据（总览 + 今日 + 近7日趋势 + 近N天 + 执行器/群 + 排行 + 部署信息） */
    public Map<String, Object> overview() {
        LocalDateTime now = LocalDateTime.now();
        String todayStart = now.toLocalDate() + " 00:00:00";
        String trendStart = now.minusDays(6).format(FMT);
        int retentionDays = retentionDays();
        String monthCut = now.minusDays(retentionDays).format(FMT);

        return MapBuilder.of(
                "generated_at", now.format(FMT),
                "retention_days", retentionDays,
                "totals", totals(todayStart),
                "today", todayStats(todayStart),
                "trend", trend(trendStart, todayStart),
                "month", monthStats(monthCut),
                "executors", executorStats(todayStart, monthCut),
                "rankings", rankings(todayStart),
                "deployment", deployment());
    }

    // ========== 总览（存量 + 全生命周期累计） ==========

    private Map<String, Object> totals(String todayStart) {
        Map<String, Object> members = jdbc.queryForMap(
                "SELECT COUNT(*) AS total,"
                        + " COALESCE(SUM(CASE WHEN status='active' THEN 1 END),0) AS active_cnt,"
                        + " COALESCE(SUM(CASE WHEN status='disabled' THEN 1 END),0) AS disabled_cnt,"
                        + " COALESCE(SUM(points),0) AS total_points,"
                        + " COALESCE(SUM(total_income),0) AS total_income,"
                        + " COALESCE(SUM(total_outcome),0) AS total_outcome FROM members");
        Integer todayNew = jdbc.queryForObject(
                "SELECT COUNT(*) FROM members WHERE created_at >= ?", Integer.class, todayStart);
        Map<String, Object> groups = jdbc.queryForMap(
                "SELECT COUNT(*) AS total,"
                        + " COALESCE(SUM(CASE WHEN status='banned' THEN 1 END),0) AS banned_cnt FROM qq_groups");
        Map<String, Object> executors = jdbc.queryForMap(
                "SELECT COUNT(*) AS total,"
                        + " COALESCE(SUM(CASE WHEN status='online' THEN 1 END),0) AS online_cnt,"
                        + " COALESCE(SUM(CASE WHEN banned_at<>'' THEN 1 END),0) AS banned_cnt FROM executors");
        Integer operators = jdbc.queryForObject(
                "SELECT COUNT(*) FROM qq_accounts WHERE status='active'", Integer.class);
        Integer plays = jdbc.queryForObject(
                "SELECT COUNT(*) FROM play_rule_files WHERE status='active'", Integer.class);

        return MapBuilder.of(
                "member_count", num(members.get("total")),
                "member_active", num(members.get("active_cnt")),
                "member_disabled", num(members.get("disabled_cnt")),
                "member_today_new", todayNew == null ? 0 : todayNew,
                "total_points", num(members.get("total_points")),
                "total_income", num(members.get("total_income")),
                "total_outcome", num(members.get("total_outcome")),
                "group_count", num(groups.get("total")),
                "group_banned", num(groups.get("banned_cnt")),
                "executor_count", num(executors.get("total")),
                "executor_online", num(executors.get("online_cnt")),
                "executor_banned", num(executors.get("banned_cnt")),
                "operator_count", operators == null ? 0 : operators,
                "play_count", plays == null ? 0 : plays);
    }

    // ========== 今日（手动/玩法分流 + 局/抽水/玩法分布） ==========

    private Map<String, Object> todayStats(String todayStart) {
        Map<String, Object> points = jdbc.queryForMap(
                "SELECT"
                        + " COALESCE(SUM(CASE WHEN delta>0 AND reason NOT LIKE ? THEN delta END),0) AS up,"
                        + " COALESCE(SUM(CASE WHEN delta<0 AND reason NOT LIKE ? THEN -delta END),0) AS down,"
                        + " COALESCE(SUM(CASE WHEN delta>0 AND reason LIKE ? THEN delta END),0) AS game_in,"
                        + " COALESCE(SUM(CASE WHEN delta<0 AND reason LIKE ? THEN -delta END),0) AS game_out,"
                        + " COUNT(CASE WHEN reason NOT LIKE ? THEN 1 END) AS manual_cnt,"
                        + " COUNT(CASE WHEN reason LIKE ? THEN 1 END) AS game_flow_cnt"
                        + " FROM point_records WHERE created_at >= ?",
                GAME_REASON_LIKE, GAME_REASON_LIKE, GAME_REASON_LIKE,
                GAME_REASON_LIKE, GAME_REASON_LIKE, GAME_REASON_LIKE, todayStart);
        Map<String, Object> games = jdbc.queryForMap(
                "SELECT COUNT(*) AS total, COALESCE(SUM(-total_delta),0) AS fee,"
                        + " COALESCE(SUM(member_count),0) AS persons,"
                        + " COALESCE(SUM(CASE WHEN warning_count>0 THEN 1 END),0) AS warn_cnt"
                        + " FROM game_records WHERE created_at >= ?", todayStart);
        Integer players = jdbc.queryForObject(
                "SELECT COUNT(DISTINCT e.qq) FROM game_record_events e"
                        + " INNER JOIN game_records r ON e.record_id=r.id WHERE r.created_at >= ?",
                Integer.class, todayStart);
        List<Map<String, Object>> playStats = jdbc.query(
                "SELECT play_name, COUNT(*) AS game_count, COALESCE(SUM(-total_delta),0) AS fee,"
                        + " COALESCE(SUM(member_count),0) AS persons,"
                        + " COALESCE(SUM(CASE WHEN warning_count>0 THEN 1 END),0) AS warn_cnt"
                        + " FROM game_records WHERE created_at >= ?"
                        + " GROUP BY play_name ORDER BY game_count DESC, play_name",
                new RowMapMapper(), todayStart);

        Map<String, Object> t = new LinkedHashMap<>();
        t.put("up", num(points.get("up")));
        t.put("down", num(points.get("down")));
        t.put("game_in", num(points.get("game_in")));
        t.put("game_out", num(points.get("game_out")));
        t.put("manual_count", num(points.get("manual_cnt")));
        t.put("game_flow_count", num(points.get("game_flow_cnt")));
        t.put("game_count", num(games.get("total")));
        t.put("fee", num(games.get("fee")));
        t.put("persons", num(games.get("persons")));
        t.put("players", players == null ? 0 : players);
        t.put("warn_games", num(games.get("warn_cnt")));
        t.put("play_stats", playStats == null ? List.of() : playStats);
        return t;
    }

    // ========== 近 7 日趋势（流水永久；局/抽水近7天恒在保留期内） ==========

    private List<Map<String, Object>> trend(String trendStart, String todayStart) {
        Map<String, long[]> manual = dayAgg(
                "SELECT SUBSTRING(created_at,1,10) AS d,"
                        + " COALESCE(SUM(CASE WHEN delta>0 THEN delta END),0) AS up,"
                        + " COALESCE(SUM(CASE WHEN delta<0 THEN -delta END),0) AS down"
                        + " FROM point_records WHERE reason NOT LIKE ? AND created_at >= ?"
                        + " GROUP BY SUBSTRING(created_at,1,10)",
                new String[]{"up", "down"}, GAME_REASON_LIKE, trendStart);
        Map<String, long[]> games = dayAgg(
                "SELECT SUBSTRING(created_at,1,10) AS d, COUNT(*) AS game_count,"
                        + " COALESCE(SUM(-total_delta),0) AS fee"
                        + " FROM game_records WHERE created_at >= ? GROUP BY SUBSTRING(created_at,1,10)",
                new String[]{"game_count", "fee"}, trendStart);
        Map<String, long[]> members = dayAgg(
                "SELECT SUBSTRING(created_at,1,10) AS d, COUNT(*) AS new_members"
                        + " FROM members WHERE created_at >= ? GROUP BY SUBSTRING(created_at,1,10)",
                new String[]{"new_members"}, trendStart);

        List<Map<String, Object>> rows = new ArrayList<>();
        // 降序：今天(index 0)在最上面，6 天前(index 6)在最下面
        for (int i = 0; i <= 6; i++) {
            String day = LocalDate.now().minusDays(i).toString();
            long[] m = manual.getOrDefault(day, new long[2]);
            long[] g = games.getOrDefault(day, new long[2]);
            long[] n = members.getOrDefault(day, new long[1]);
            rows.add(MapBuilder.of(
                    "date", day, "up", m[0], "down", m[1],
                    "game_count", g[0], "fee", g[1], "new_members", n[0]));
        }
        return rows;
    }

    /** 按天聚合：cols 顺序与 SELECT 数值列一致（除第一列 d 外），返回 date → 数值数组 */
    private Map<String, long[]> dayAgg(String sql, String[] cols, Object... args) {
        Map<String, long[]> out = new LinkedHashMap<>();
        for (Map<String, Object> row : jdbc.query(sql, new RowMapMapper(), args)) {
            String day = String.valueOf(row.get("d"));
            long[] v = new long[cols.length];
            for (int i = 0; i < cols.length; i++) {
                Object o = row.get(cols[i]);
                v[i] = o == null ? 0 : ((Number) o).longValue();
            }
            out.put(day, v);
        }
        return out;
    }

    // ========== 近 N 天汇总（game_records 受保留期，cutoff 与清理一致） ==========

    private Map<String, Object> monthStats(String monthCut) {
        Map<String, Object> games = jdbc.queryForMap(
                "SELECT COUNT(*) AS total, COALESCE(SUM(-total_delta),0) AS fee,"
                        + " COALESCE(SUM(member_count),0) AS persons,"
                        + " COALESCE(SUM(CASE WHEN warning_count>0 THEN 1 END),0) AS warn_cnt"
                        + " FROM game_records WHERE created_at >= ?", monthCut);
        return MapBuilder.of(
                "game_count", num(games.get("total")),
                "fee", num(games.get("fee")),
                "persons", num(games.get("persons")),
                "warn_games", num(games.get("warn_cnt")));
    }

    // ========== 部署信息（服务器 + 数据库，供页面/日报存档展示） ==========

    private Map<String, Object> deployment() {
        Map<String, Object> d = dbDeployment();
        return MapBuilder.of(
                "server_ip", localIp(),
                "host_name", hostName(),
                "os", System.getProperty("os.name") + " " + System.getProperty("os.version")
                        + " (" + System.getProperty("os.arch") + ")",
                "java_version", System.getProperty("java.version"),
                "base_dir", System.getProperty("user.dir"),
                "server_port", env.getProperty("server.port", "8892"),
                "profile", env.getActiveProfiles().length == 0 ? "-"
                        : String.join(",", env.getActiveProfiles()),
                "db_host", d.getOrDefault("host", ""),
                "db_name", d.getOrDefault("name", ""),
                "db_version", d.getOrDefault("version", ""));
    }

    /** 数据库部署：连接池真实 URL 解析出主机/库名 + SELECT VERSION()（MySQL 服务器版本；H2 测库亦可） */
    private Map<String, Object> dbDeployment() {
        Map<String, Object> out = new LinkedHashMap<>();
        String url = null;
        try (Connection c = dataSource.getConnection()) {
            url = c.getMetaData().getURL();
            out.put("version", String.valueOf(c.getMetaData().getDatabaseProductVersion()));
        } catch (Exception e) {
            out.put("version", "");
        }
        String host = "", name = "";
        if (url != null) {
            String u = url.split("\\?", 2)[0]; // 去查询参数（MySQL 的 ?k=v&…；H2 无）
            int scheme = u.indexOf("://");
            if (scheme >= 0) {
                String rest = u.substring(scheme + 3);
                int slash = rest.indexOf('/');
                host = slash >= 0 ? rest.substring(0, slash) : rest;
                name = slash >= 0 ? rest.substring(slash + 1) : "";
            } else { // jdbc:h2:mem:xxx;PARAM → 取最后一个冒号后到分号前
                int colon = u.lastIndexOf(':');
                String tail = colon >= 0 ? u.substring(colon + 1) : "";
                int semi = tail.indexOf(';');
                name = semi >= 0 ? tail.substring(0, semi) : tail;
                host = "内置内存库(H2)";
            }
            if (name.endsWith("/")) name = name.substring(0, name.length() - 1);
        }
        out.put("host", host);
        out.put("name", name);
        return out;
    }

    /** 本机 IPv4（优先非回环网卡；取不到则回退 127.0.0.1，展示用，不保证公网地址） */
    private String localIp() {
        try {
            String addr = InetAddress.getLocalHost().getHostAddress();
            if (!addr.startsWith("127.")) return addr;
            for (Enumeration<NetworkInterface> nets = NetworkInterface.getNetworkInterfaces();
                 nets.hasMoreElements(); ) {
                NetworkInterface n = nets.nextElement();
                if (!n.isUp() || n.isLoopback()) continue;
                for (InterfaceAddress ia : n.getInterfaceAddresses()) {
                    if (ia.getAddress() instanceof java.net.Inet4Address) {
                        return ia.getAddress().getHostAddress();
                    }
                }
            }
        } catch (Exception ignored) {
        }
        return "127.0.0.1";
    }

    private String hostName() {
        try {
            return InetAddress.getLocalHost().getHostName();
        } catch (Exception e) {
            return "";
        }
    }

    // ========== 执行器与群明细 ==========

    private List<Map<String, Object>> executorStats(String todayStart, String monthCut) {
        List<Map<String, Object>> executors = jdbc.query(
                "SELECT id, name, status, version, host, admin_qq, last_heartbeat, last_ip,"
                        + " banned_at, ban_reason, game_fee_rate FROM executors ORDER BY id",
                new RowMapMapper());
        // 群归属：executor_id → 群行；未绑定（executor_id=0）另收，末尾以独立行呈现
        Map<Long, List<Map<String, Object>>> groupsByExe = new LinkedHashMap<>();
        List<Map<String, Object>> orphanGroups = new ArrayList<>();
        for (Map<String, Object> g : jdbc.query(
                "SELECT group_id, group_name, status, ban_reason, member_count, executor_id"
                        + " FROM qq_groups ORDER BY group_id", new RowMapMapper())) {
            Object eid = g.get("executor_id");
            if (eid != null && ((Number) eid).longValue() > 0) {
                groupsByExe.computeIfAbsent(((Number) eid).longValue(), k -> new ArrayList<>()).add(g);
            } else {
                orphanGroups.add(g);
            }
        }
        Map<String, Map<String, Object>> exeToday = gameGroupAgg("executor_id", todayStart);
        Map<String, Map<String, Object>> exeMonth = gameMonthAgg("executor_id", monthCut);
        Map<String, Map<String, Object>> groupToday = gameGroupAgg("group_id", todayStart);
        Map<String, Long> groupMembers = groupedCount("SELECT group_id, COUNT(*) AS c FROM members"
                + " WHERE group_id<>'' GROUP BY group_id");

        List<Map<String, Object>> rows = new ArrayList<>();
        for (Map<String, Object> ex : executors) {
            String key = String.valueOf(ex.get("id"));
            Map<String, Object> today = exeToday.getOrDefault(key, MapBuilder.of());
            Map<String, Object> month = exeMonth.getOrDefault(key, MapBuilder.of());
            boolean banned = !String.valueOf(ex.get("banned_at")).isEmpty();
            Map<String, Object> row = MapBuilder.of(
                    "id", num(ex.get("id")),
                    "name", String.valueOf(ex.get("name")),
                    "status", banned ? "banned" : String.valueOf(ex.get("status")),
                    "banned", banned,
                    "ban_reason", String.valueOf(ex.get("ban_reason")),
                    "version", String.valueOf(ex.get("version")),
                    "host", String.valueOf(ex.get("host")),
                    "admin_qq", String.valueOf(ex.get("admin_qq")),
                    "last_heartbeat", String.valueOf(ex.get("last_heartbeat")),
                    "last_ip", String.valueOf(ex.get("last_ip")),
                    "game_fee_rate", num(ex.get("game_fee_rate")),
                    "today_game_count", num(today.get("total")),
                    "today_fee", num(today.get("fee")),
                    "today_persons", num(today.get("persons")),
                    "today_warn_games", num(today.get("warn_cnt")),
                    "month_game_count", num(month.get("total")),
                    "month_fee", num(month.get("fee")));
            List<Map<String, Object>> detail = new ArrayList<>();
            for (Map<String, Object> g : groupsByExe.getOrDefault((Long) ex.get("id"), new ArrayList<>())) {
                detail.add(groupRow(g, groupToday, groupMembers));
            }
            row.put("groups", detail);
            row.put("group_count", detail.size());
            rows.add(row);
        }
        // 未绑定执行器的群（executor_id=0）：独立成行，保证每个群都能在报表里看到
        if (!orphanGroups.isEmpty()) {
            Map<String, Object> orphan = MapBuilder.of(
                    "id", 0L, "name", "（未绑定执行器）", "status", "orphan", "banned", false,
                    "ban_reason", "", "version", "", "host", "", "admin_qq", "",
                    "last_heartbeat", "", "last_ip", "", "game_fee_rate", 0,
                    "today_game_count", 0, "today_fee", 0, "today_persons", 0, "today_warn_games", 0,
                    "month_game_count", 0, "month_fee", 0);
            List<Map<String, Object>> detail = new ArrayList<>();
            for (Map<String, Object> g : orphanGroups) {
                detail.add(groupRow(g, groupToday, groupMembers));
            }
            orphan.put("groups", detail);
            orphan.put("group_count", detail.size());
            rows.add(orphan);
        }
        return rows;
    }

    /** 群明细行：今日局数/抽水 + 群状态/人数 + 建档会员数 */
    private Map<String, Object> groupRow(Map<String, Object> g,
                                         Map<String, Map<String, Object>> groupToday,
                                         Map<String, Long> groupMembers) {
        String gid = String.valueOf(g.get("group_id"));
        Map<String, Object> today = groupToday.getOrDefault(gid, MapBuilder.of());
        return MapBuilder.of(
                "group_id", gid,
                "group_name", String.valueOf(g.get("group_name")),
                "status", String.valueOf(g.get("status")),
                "ban_reason", String.valueOf(g.get("ban_reason")),
                "qq_member_count", num(g.get("member_count")),
                "member_registered", groupMembers.getOrDefault(gid, 0L),
                "today_game_count", num(today.get("total")),
                "today_fee", num(today.get("fee")));
    }

    /** 按列分组的今日局聚合：col ∈ {executor_id, group_id}（内部常量，无注入面） */
    private Map<String, Map<String, Object>> gameGroupAgg(String col, String since) {
        Map<String, Map<String, Object>> out = new LinkedHashMap<>();
        List<Map<String, Object>> rows = jdbc.query(
                "SELECT " + col + " AS k, COUNT(*) AS total, COALESCE(SUM(-total_delta),0) AS fee,"
                        + " COALESCE(SUM(member_count),0) AS persons,"
                        + " COALESCE(SUM(CASE WHEN warning_count>0 THEN 1 END),0) AS warn_cnt"
                        + " FROM game_records WHERE created_at >= ? GROUP BY " + col,
                new RowMapMapper(), since);
        for (Map<String, Object> r : rows) {
            out.put(String.valueOf(r.get("k")), MapBuilder.of(
                    "total", num(r.get("total")), "fee", num(r.get("fee")),
                    "persons", num(r.get("persons")), "warn_cnt", num(r.get("warn_cnt"))));
        }
        return out;
    }

    /** 按列分组的近N天局聚合（列同上，只取局数与抽水） */
    private Map<String, Map<String, Object>> gameMonthAgg(String col, String since) {
        Map<String, Map<String, Object>> out = new LinkedHashMap<>();
        List<Map<String, Object>> rows = jdbc.query(
                "SELECT " + col + " AS k, COUNT(*) AS total, COALESCE(SUM(-total_delta),0) AS fee"
                        + " FROM game_records WHERE created_at >= ? GROUP BY " + col,
                new RowMapMapper(), since);
        for (Map<String, Object> r : rows) {
            out.put(String.valueOf(r.get("k")), MapBuilder.of(
                    "total", num(r.get("total")), "fee", num(r.get("fee"))));
        }
        return out;
    }

    private Map<String, Long> groupedCount(String sql) {
        Map<String, Long> out = new LinkedHashMap<>();
        for (Map<String, Object> r : jdbc.query(sql, new RowMapMapper())) {
            out.put(String.valueOf(r.get("group_id")), num(r.get("c")));
        }
        return out;
    }

    // ========== 排行榜（各 Top10） ==========

    private Map<String, Object> rankings(String todayStart) {
        Map<String, Object> r = new LinkedHashMap<>();
        r.put("up_today", rankList(true, false, todayStart));
        r.put("down_today", rankList(false, false, todayStart));
        r.put("win_today", rankList(true, true, todayStart));
        r.put("lose_today", rankList(false, true, todayStart));
        r.put("top_points", jdbc.query(
                "SELECT qq, nickname, points, status FROM members"
                        + " ORDER BY points DESC, qq ASC LIMIT " + RANK_LIMIT, new RowMapMapper()));
        return r;
    }

    /** 单日流水榜：positive=统计正delta / 负delta(取绝对值)；game=玩法结算 / 手动资金 */
    private List<Map<String, Object>> rankList(boolean positive, boolean game, String since) {
        return jdbc.query(
                "SELECT pr.qq, MAX(m.nickname) AS nickname,"
                        + " SUM(" + (positive ? "delta" : "-delta") + ") AS amount, COUNT(*) AS flow_count"
                        + " FROM point_records pr LEFT JOIN members m ON m.qq=pr.qq"
                        + " WHERE pr.delta " + (positive ? "> 0" : "< 0")
                        + " AND pr.reason " + (game ? "LIKE ?" : "NOT LIKE ?") + " AND pr.created_at >= ?"
                        + " GROUP BY pr.qq ORDER BY amount DESC, pr.qq ASC LIMIT " + RANK_LIMIT,
                new RowMapMapper(), GAME_REASON_LIKE, since);
    }

    // ========== 内部 ==========

    /** 有效保留天数（与 RetentionCleanupService 同实现）：配置键 game_record_retention_days，默认30 */
    private int retentionDays() {
        String v = dictService.getValue("game_record_retention_days");
        if (v == null || v.trim().isEmpty()) return 30;
        try {
            int days = Integer.parseInt(v.trim());
            return Math.max(1, Math.min(days, 3650));
        } catch (NumberFormatException e) {
            return 30;
        }
    }

    private long num(Object o) {
        return o == null ? 0 : ((Number) o).longValue();
    }
}
