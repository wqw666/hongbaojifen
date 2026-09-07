package com.hbjf.api;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 报表展示测试：手动/玩法分流口径、抽水=SUM(−total_delta)、今日/昨日趋势、
 * 近N天保留口径、执行器与群明细、未绑定执行器群、排行榜 Top 截断、空库零值安全
 */
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class ReportTest {

    @Autowired
    MockMvc mvc;
    @Autowired
    JdbcTemplate jdbc;
    @Autowired
    ObjectMapper om;

    private static final DateTimeFormatter FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    String adminToken;
    String exeToken;
    String now;
    String yesterday;

    @BeforeEach
    void setUp() throws Exception {
        jdbc.update("DELETE FROM executor_commands");
        jdbc.update("DELETE FROM executors");
        jdbc.update("TRUNCATE TABLE game_record_events");
        jdbc.update("TRUNCATE TABLE game_records");
        jdbc.update("TRUNCATE TABLE point_records");
        jdbc.update("TRUNCATE TABLE members");
        jdbc.update("TRUNCATE TABLE qq_accounts");
        jdbc.update("TRUNCATE TABLE qq_groups");

        now = LocalDateTime.now().format(FMT);
        yesterday = LocalDateTime.now().minusDays(1).format(FMT);

        MvcResult login = mvc.perform(post("/api/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"admin\",\"password\":\"admin123\"}"))
                .andExpect(status().isOk()).andReturn();
        adminToken = om.readTree(login.getResponse().getContentAsString()).get("data").get("token").asText();

        MvcResult created = mvc.perform(post("/api/admin/executors")
                        .header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"exe-report-1\"}"))
                .andExpect(status().isOk()).andReturn();
        exeToken = om.readTree(created.getResponse().getContentAsString()).get("data").get("token_plain").asText();
        mvc.perform(post("/api/open/executor/heartbeat")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"token\":\"" + exeToken + "\",\"admin_qq\":\"91003\",\"admin_nickname\":\"小C\"}"))
                .andExpect(status().isOk()).andReturn();
        // DELETE 不重置自增：全量跑时 executors.id 未必是 1 → 动态取真实 id 再绑定群/昨日对局
        long exeId = jdbc.queryForObject("SELECT id FROM executors WHERE name='exe-report-1'", Long.class);

        // 群：70001 绑定执行器(正常)；70002 未绑定(封禁) → 报表应有孤儿行
        jdbc.update("INSERT INTO qq_groups (group_id, group_name, member_count, status, executor_id, created_at, updated_at)"
                + " VALUES ('70001','红包群1',50,'active',?,?,?)", exeId, yesterday, yesterday);
        jdbc.update("INSERT INTO qq_groups (group_id, group_name, member_count, status, ban_reason, executor_id, created_at, updated_at)"
                + " VALUES ('70002','红包群2',30,'banned','违规',0,?,?)", yesterday, yesterday);

        // 会员：A/B/D 昨天建档(含停用 D)，C 今天建档
        jdbc.update("INSERT INTO members (qq, nickname, points, total_income, total_outcome, group_id, status, created_at, updated_at)"
                + " VALUES ('30001','张三',300,300,0,'70001','active',?,?)", yesterday, yesterday);
        jdbc.update("INSERT INTO members (qq, nickname, points, total_income, total_outcome, group_id, status, created_at, updated_at)"
                + " VALUES ('30002','李四',0,0,0,'70001','active',?,?)", yesterday, yesterday);
        jdbc.update("INSERT INTO members (qq, nickname, points, total_income, total_outcome, group_id, status, note, created_at, updated_at)"
                + " VALUES ('30004','王六',500,500,0,'70002','disabled','停用',?,?)", yesterday, yesterday);
        jdbc.update("INSERT INTO members (qq, nickname, points, total_income, total_outcome, group_id, status, created_at, updated_at)"
                + " VALUES ('30003','王五',0,0,0,'70001','active',?,?)", now, now);

        // 今日手动资金：A 上分1000、B 上分500、A 提现200（口径：reason 非 玩法:%）
        manual("30001", 1000, "微信收款-充值", "m-up-1");
        manual("30002", 500, "充值", "m-up-2");
        manual("30001", -200, "提现", "m-down-1");
        // 同步 member 累计列（真实流水中由服务端维护；这里直插后手工对齐）
        jdbc.update("UPDATE members SET points=?, total_income=?, total_outcome=? WHERE qq='30001'", 1100, 1300, 200);
        jdbc.update("UPDATE members SET points=?, total_income=? WHERE qq='30002'", 500, 500);

        // 昨日手动上分 300（直插，验证趋势昨日行）
        jdbc.update("INSERT INTO point_records (qq, member_id, delta, type, reason, operator, biz_no, created_at)"
                + " VALUES ('30001',(SELECT id FROM members WHERE qq='30001'),300,'INCOME','收款-补录','open','m-y1',?)", yesterday);

        // 今日对局：A +50、B −80 → total_delta=−30 → 抽水30（守恒：输家多输 = 抽水）
        mvc.perform(post("/api/open/games/report")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"executor_token\":\"" + exeToken + "\",\"round_id\":\"R-REPORT-T1\","
                                + "\"play_id\":\"1\",\"play_name\":\"复合玩法\",\"group_id\":\"70001\","
                                + "\"events\":[{\"qq\":\"30001\",\"nickname\":\"张三\",\"delta\":50},"
                                + "{\"qq\":\"30002\",\"nickname\":\"李四\",\"delta\":-80}]}"))
                .andExpect(status().isOk()).andReturn();
        jdbc.update("UPDATE members SET points=1150, total_income=1350 WHERE qq='30001'");
        jdbc.update("UPDATE members SET points=420, total_outcome=80 WHERE qq='30002'");

        // 昨日对局（直插）：member_count2、total_delta−10 → 昨日抽水10
        jdbc.update("INSERT INTO game_records (round_id, play_id, play_name, group_id, executor_id, executor_name,"
                        + " operator_qq, member_count, total_delta, event_count, warning, warning_count, created_at)"
                        + " VALUES ('R-REPORT-Y1',1,'复合玩法','70001',?, 'exe-report-1','91003',2,-10,2,'',0,?)",
                exeId, yesterday);
    }

    private void manual(String qq, int delta, String reason, String bizNo) throws Exception {
        String points = String.valueOf(Math.abs(delta));
        String action = delta > 0 ? "up" : "down";
        mvc.perform(post("/api/open/points/" + action)
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"executor_token\":\"" + exeToken + "\",\"qq\":\"" + qq
                                + "\",\"points\":" + points + ",\"reason\":\"" + reason + "\",\"bizNo\":\"" + bizNo + "\"}"))
                .andExpect(status().isOk()).andReturn();
    }

    private JsonNode overview() throws Exception {
        MvcResult result = mvc.perform(get("/api/admin/report/overview")
                        .header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isOk()).andReturn();
        return om.readTree(result.getResponse().getContentAsString()).get("data");
    }

    @Test
    void overviewAggregatesWithManualGameSplitAndOrphanGroup() throws Exception {
        JsonNode data = overview();

        // ---- totals（存量 + 全生命周期累计） ----
        JsonNode totals = data.get("totals");
        assertThat(totals.get("member_count").asInt()).isEqualTo(4);
        assertThat(totals.get("member_active").asInt()).isEqualTo(3);
        assertThat(totals.get("member_disabled").asInt()).isEqualTo(1);
        assertThat(totals.get("member_today_new").asInt()).isEqualTo(1);      // 仅 30003 今天建档
        assertThat(totals.get("total_points").asLong()).isEqualTo(2070);      // 1150+420+500+0
        assertThat(totals.get("total_income").asLong()).isEqualTo(2350);      // 1350+500+500
        assertThat(totals.get("total_outcome").asLong()).isEqualTo(280);
        assertThat(totals.get("group_count").asInt()).isEqualTo(2);
        assertThat(totals.get("group_banned").asInt()).isEqualTo(1);
        assertThat(totals.get("executor_count").asInt()).isEqualTo(1);
        assertThat(totals.get("executor_online").asInt()).isEqualTo(1);
        assertThat(totals.get("operator_count").asInt()).isEqualTo(1);        // 心跳登记的 91003
        assertThat(totals.get("play_count").asInt()).isGreaterThanOrEqualTo(0);

        // ---- today（手动 vs 玩法分流） ----
        JsonNode today = data.get("today");
        assertThat(today.get("up").asLong()).isEqualTo(1500);        // 手动上分 1000+500
        assertThat(today.get("down").asLong()).isEqualTo(200);       // 手动提现 200
        assertThat(today.get("game_in").asLong()).isEqualTo(50);     // 玩法赢家 A+50
        assertThat(today.get("game_out").asLong()).isEqualTo(80);    // 玩法输家 B−80
        assertThat(today.get("manual_count").asInt()).isEqualTo(3);  // 3 笔手动
        assertThat(today.get("game_flow_count").asInt()).isEqualTo(2); // 2 笔玩法流水
        assertThat(today.get("game_count").asInt()).isEqualTo(1);    // 今日 1 局
        assertThat(today.get("fee").asLong()).isEqualTo(30);         // 抽水 = −Σtotal_delta
        assertThat(today.get("persons").asInt()).isEqualTo(2);       // 本局结算人次 2
        assertThat(today.get("players").asInt()).isEqualTo(2);       // 去重玩家 2
        assertThat(today.get("warn_games").asInt()).isZero();
        JsonNode playStats = today.get("play_stats");
        assertThat(playStats.size()).isEqualTo(1);
        assertThat(playStats.get(0).get("play_name").asText()).isEqualTo("复合玩法");
        assertThat(playStats.get(0).get("fee").asLong()).isEqualTo(30);

        // ---- trend：7 行，降序 = 今天在最上（index 0），6 天前在最下（index 6） ----
        JsonNode trend = data.get("trend");
        assertThat(trend.size()).isEqualTo(7);
        String todayDate = LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyy-MM-dd"));
        String sixDaysAgo = LocalDateTime.now().minusDays(6).format(DateTimeFormatter.ofPattern("yyyy-MM-dd"));
        assertThat(trend.get(0).get("date").asText()).isEqualTo(todayDate);
        assertThat(trend.get(6).get("date").asText()).isEqualTo(sixDaysAgo);
        JsonNode todayRow = trend.get(0);       // 今天在最上面
        assertThat(todayRow.get("up").asLong()).isEqualTo(1500);
        assertThat(todayRow.get("down").asLong()).isEqualTo(200);
        assertThat(todayRow.get("game_count").asInt()).isEqualTo(1);
        assertThat(todayRow.get("fee").asLong()).isEqualTo(30);
        assertThat(todayRow.get("new_members").asInt()).isEqualTo(1);
        JsonNode yesterdayRow = trend.get(1);
        assertThat(yesterdayRow.get("up").asLong()).isEqualTo(300);
        assertThat(yesterdayRow.get("game_count").asInt()).isEqualTo(1);
        assertThat(yesterdayRow.get("fee").asLong()).isEqualTo(10);
        assertThat(yesterdayRow.get("new_members").asInt()).isEqualTo(3); // A/B/D

        // ---- month（近N天，含昨日对局） ----
        JsonNode month = data.get("month");
        assertThat(month.get("game_count").asInt()).isEqualTo(2);
        assertThat(month.get("fee").asLong()).isEqualTo(40);
        assertThat(month.get("persons").asInt()).isEqualTo(4);
        assertThat(month.get("warn_games").asInt()).isZero();

        // ---- executors：绑定执行器行 + 未绑定群孤儿行 ----
        JsonNode executors = data.get("executors");
        assertThat(executors.size()).isEqualTo(2);
        JsonNode exe = executors.get(0);
        assertThat(exe.get("name").asText()).isEqualTo("exe-report-1");
        assertThat(exe.get("status").asText()).isEqualTo("online");
        assertThat(exe.get("banned").asBoolean()).isFalse();
        assertThat(exe.get("admin_qq").asText()).isEqualTo("91003");
        assertThat(exe.get("game_fee_rate").asInt()).isEqualTo(20);
        assertThat(exe.get("today_game_count").asInt()).isEqualTo(1);
        assertThat(exe.get("today_fee").asLong()).isEqualTo(30);
        assertThat(exe.get("today_persons").asInt()).isEqualTo(2);
        assertThat(exe.get("month_game_count").asInt()).isEqualTo(2);
        assertThat(exe.get("month_fee").asLong()).isEqualTo(40);
        assertThat(exe.get("group_count").asInt()).isEqualTo(1);
        JsonNode grp = exe.get("groups").get(0);
        assertThat(grp.get("group_id").asText()).isEqualTo("70001");
        assertThat(grp.get("qq_member_count").asInt()).isEqualTo(50);   // QQ 群真实人数
        assertThat(grp.get("member_registered").asInt()).isEqualTo(3);  // 建档会员 A/B/C
        assertThat(grp.get("today_game_count").asInt()).isEqualTo(1);
        assertThat(grp.get("today_fee").asLong()).isEqualTo(30);
        JsonNode orphan = executors.get(1);
        assertThat(orphan.get("name").asText()).contains("未绑定");
        assertThat(orphan.get("status").asText()).isEqualTo("orphan");
        assertThat(orphan.get("groups").get(0).get("group_id").asText()).isEqualTo("70002");
        assertThat(orphan.get("groups").get(0).get("status").asText()).isEqualTo("banned");
        assertThat(orphan.get("groups").get(0).get("member_registered").asInt()).isEqualTo(1); // D

        // ---- rankings ----
        JsonNode rankings = data.get("rankings");
        assertThat(rankings.get("up_today").size()).isEqualTo(2);
        assertThat(rankings.get("up_today").get(0).get("qq").asText()).isEqualTo("30001");
        assertThat(rankings.get("up_today").get(0).get("amount").asLong()).isEqualTo(1000);
        assertThat(rankings.get("up_today").get(0).get("nickname").asText()).isEqualTo("张三");
        assertThat(rankings.get("down_today").get(0).get("amount").asLong()).isEqualTo(200);
        assertThat(rankings.get("win_today").get(0).get("amount").asLong()).isEqualTo(50);
        assertThat(rankings.get("lose_today").get(0).get("qq").asText()).isEqualTo("30002");
        assertThat(rankings.get("lose_today").get(0).get("amount").asLong()).isEqualTo(80);
        JsonNode topPoints = rankings.get("top_points");
        assertThat(topPoints.size()).isEqualTo(4);          // 不足10也全量
        assertThat(topPoints.get(0).get("qq").asText()).isEqualTo("30001");
        assertThat(topPoints.get(0).get("points").asLong()).isEqualTo(1150);
        assertThat(topPoints.get(3).get("qq").asText()).isEqualTo("30003");
        // 手动榜不含玩法流水
        for (JsonNode n : rankings.get("up_today")) {
            assertThat(n.get("amount").asLong()).isLessThanOrEqualTo(1000);
        }

        // ---- deployment（部署信息）：主机/系统/JDK + 数据库地址与版本 ----
        JsonNode dep = data.get("deployment");
        assertThat(dep.get("server_ip").asText()).isNotBlank();
        assertThat(dep.get("os").asText()).isNotBlank();
        assertThat(dep.get("java_version").asText()).isNotBlank();
        assertThat(dep.get("server_port").asText()).isNotBlank();
        assertThat(dep.get("db_host").asText()).isNotBlank();
        assertThat(dep.get("db_name").asText()).isEqualTo("testdb");  // 测试库 H2 mem
        assertThat(dep.get("db_version").asText()).isNotBlank();
    }

    @Test
    void emptyDatabaseReturnsZeroesNotErrors() throws Exception {
        // setUp 已灌数据 → 本测试先清空，验证空库零值安全（JWT 无状态，token 仍有效）
        jdbc.update("DELETE FROM executors");
        jdbc.update("TRUNCATE TABLE game_record_events");
        jdbc.update("TRUNCATE TABLE game_records");
        jdbc.update("TRUNCATE TABLE point_records");
        jdbc.update("TRUNCATE TABLE members");
        jdbc.update("TRUNCATE TABLE qq_accounts");
        jdbc.update("TRUNCATE TABLE qq_groups");
        JsonNode data = overview();
        JsonNode totals = data.get("totals");
        assertThat(totals.get("member_count").asInt()).isZero();
        assertThat(totals.get("total_points").asLong()).isZero();
        JsonNode today = data.get("today");
        assertThat(today.get("up").asLong()).isZero();
        assertThat(today.get("game_count").asInt()).isZero();
        assertThat(today.get("play_stats").isEmpty()).isTrue();
        assertThat(data.get("trend").size()).isEqualTo(7);   // 空库也出 7 天零行
        assertThat(data.get("executors").isEmpty()).isTrue();
        assertThat(data.get("rankings").get("up_today").isEmpty()).isTrue();
        assertThat(data.get("rankings").get("top_points").isEmpty()).isTrue();
        assertThat(data.get("month").get("fee").asLong()).isZero();
        assertThat(totals.get("play_count").asInt()).isGreaterThanOrEqualTo(0);
    }

    @Test
    void overviewRequiresAuth() throws Exception {
        mvc.perform(get("/api/admin/report/overview")).andExpect(status().isUnauthorized());
    }
}
