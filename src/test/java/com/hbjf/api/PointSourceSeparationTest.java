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

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 积分来源分离测试（V1.0.14）
 *
 * 「操作改分」与「游戏结算改分」在 point_records.source 上分开落库，会员与流水按来源统计：
 *   manual  后台手动上下分（含 open 接口历史开放语义）
 *   approve 群内审批通过（agent 积分审批）
 *   game    玩法结算（GameRecordService 对局入账）
 *
 * 覆盖：三类来源各自的落库字段与流水额、外部不能伪造 game 来源、会员列表 6 个来源聚合、
 *       流水抽屉 summary（全量口径）与来源过滤、报表口径改读 source（不再依赖 reason 文案）
 */
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class PointSourceSeparationTest {

    @Autowired
    MockMvc mvc;
    @Autowired
    JdbcTemplate jdbc;
    @Autowired
    ObjectMapper om;

    String adminToken;
    String exeToken;
    long memberId;      // 30001 张三

    @BeforeEach
    void setUp() throws Exception {
        jdbc.update("DELETE FROM executor_commands");
        jdbc.update("DELETE FROM executors");
        jdbc.update("TRUNCATE TABLE game_record_events");
        jdbc.update("TRUNCATE TABLE game_records");
        jdbc.update("TRUNCATE TABLE point_records");
        jdbc.update("TRUNCATE TABLE qq_groups");
        jdbc.update("TRUNCATE TABLE members");
        jdbc.update("TRUNCATE TABLE qq_accounts");

        MvcResult login = mvc.perform(post("/api/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"admin\",\"password\":\"admin123\"}"))
                .andExpect(status().isOk()).andReturn();
        adminToken = om.readTree(login.getResponse().getContentAsString()).get("data").get("token").asText();

        MvcResult created = mvc.perform(post("/api/admin/executors")
                        .header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"exe-src-1\"}"))
                .andExpect(status().isOk()).andReturn();
        exeToken = om.readTree(created.getResponse().getContentAsString()).get("data").get("token_plain").asText();

        mvc.perform(post("/api/open/executor/heartbeat")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"token\":\"" + exeToken + "\",\"admin_qq\":\"91003\",\"admin_nickname\":\"小C\"}"))
                .andExpect(status().isOk()).andReturn();

        // 30001 建档案（走审批通道需已建档）、30002 参与对局、30003 验证零流水会员
        mvc.perform(post("/api/open/members/batch")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"executor_token\":\"" + exeToken + "\",\"members\":[{\"qq\":\"30001\",\"nickname\":\"张三\"},"
                                + "{\"qq\":\"30002\",\"nickname\":\"李四\"},{\"qq\":\"30003\",\"nickname\":\"王五\"}]}"))
                .andExpect(status().isOk()).andReturn();
        // 30002 预置余额：本用例只关注来源字段，避免「余额不足」把对局事件整条跳过
        jdbc.update("UPDATE members SET points=1000 WHERE qq='30002'");
        memberId = jdbc.queryForObject("SELECT id FROM members WHERE qq='30001'", Long.class);
    }

    // ========== 逐来源落库 ==========

    @Test
    void manualAdjustKeepsManualSourceAndFullFlow() throws Exception {
        // 后台手动上分 100 / 下分 30
        adminAdjust(100, "微信充值-充值");
        adminAdjust(-30, "提现");

        assertThat(recordCount("source='manual'")).isEqualTo(2);
        Map<String, Object> up = record(100);
        assertThat(up.get("source")).isEqualTo("manual");
        assertThat(((Number) up.get("flow_amount")).longValue()).isEqualTo(100L);
        assertThat(up.get("type")).isEqualTo("INCOME");
        Map<String, Object> down = record(-30);
        assertThat(down.get("source")).isEqualTo("manual");
        // 手动下分：流水额全额记（与积分同额，负号保留）
        assertThat(((Number) down.get("flow_amount")).longValue()).isEqualTo(-30L);
        assertThat(down.get("type")).isEqualTo("OUTCOME");
        assertThat(jdbc.queryForObject("SELECT points FROM members WHERE qq='30001'", Long.class)).isEqualTo(70L);
    }

    @Test
    void approvePathWritesApproveSource() throws Exception {
        openAdjust("/api/open/points/up", 50, "approve", "approve:1");
        openAdjust("/api/open/points/down", 20, "approve", "approve:2");

        assertThat(recordCount("source='approve'")).isEqualTo(2);
        assertThat(recordCount("source='manual'")).isZero();
        Map<String, Object> up = record(50);
        assertThat(up.get("source")).isEqualTo("approve");
        assertThat(up.get("operator")).isEqualTo("executor:exe-src-1");
        assertThat(((Number) up.get("flow_amount")).longValue()).isEqualTo(50L);
        assertThat(jdbc.queryForObject("SELECT points FROM members WHERE qq='30001'", Long.class)).isEqualTo(30L);
    }

    @Test
    void openApiWithoutSourceIsManualAndGameSourceCannotBeForged() throws Exception {
        // 不带 source：历史开放语义 → manual
        openAdjust("/api/open/points/up", 10, null, "open-1");
        // 伪造 source=game：白名单外一律归一 manual（game 只能由对局结算写）
        openAdjust("/api/open/points/up", 20, "game", "open-2");
        openAdjust("/api/open/points/up", 30, "hack", "open-3");

        assertThat(recordCount("source='manual'")).isEqualTo(3);
        assertThat(recordCount("source='game'")).isZero();
    }

    @Test
    void gameSettlementWritesGameSource() throws Exception {
        reportRound("R-SRC-1", "[{\"qq\":\"30001\",\"nickname\":\"张三\",\"delta\":80,\"flow\":40},"
                + "{\"qq\":\"30002\",\"nickname\":\"李四\",\"delta\":-90,\"flow\":50}]");

        assertThat(recordCount("source='game'")).isEqualTo(2);
        Map<String, Object> ev = record(80);
        assertThat(ev.get("source")).isEqualTo("game");
        assertThat(ev.get("reason").toString()).startsWith("玩法:");
        assertThat(ev.get("operator")).isEqualTo("executor:exe-src-1");
        assertThat(((Number) ev.get("flow_amount")).longValue()).isEqualTo(40L);
    }

    // ========== 会员列表：按来源聚合 ==========

    @Test
    void memberListAggregatesPointsAndFlowBySource() throws Exception {
        adminAdjust(100, "充值");                 // manual  +100
        openAdjust("/api/open/points/up", 50, "approve", "approve:list-1");
        openAdjust("/api/open/points/down", 20, "approve", "approve:list-2");
        reportRound("R-SRC-2", "[{\"qq\":\"30001\",\"nickname\":\"张三\",\"delta\":80,\"flow\":40},"
                + "{\"qq\":\"30002\",\"nickname\":\"李四\",\"delta\":-80,\"flow\":40}]");
        // 30002 只输 80：本局净负 = 抽水（−80 + 80 = 0）
        reportRound("R-SRC-3", "[{\"qq\":\"30001\",\"nickname\":\"张三\",\"delta\":-30,\"flow\":15},"
                + "{\"qq\":\"30002\",\"nickname\":\"李四\",\"delta\":25,\"flow\":15}]");

        JsonNode row = memberRow("30001");
        assertThat(row.get("manual_income").asLong()).isEqualTo(100);
        assertThat(row.get("manual_outcome").asLong()).isZero();
        assertThat(row.get("approve_income").asLong()).isEqualTo(50);
        assertThat(row.get("approve_outcome").asLong()).isEqualTo(20);
        assertThat(row.get("game_income").asLong()).isEqualTo(80);
        assertThat(row.get("game_outcome").asLong()).isEqualTo(30);
        assertThat(row.get("game_flow").asLong()).isEqualTo(40 + 15);     // 撑庄半额流水
        assertThat(row.get("points").asLong()).isEqualTo(100 + 50 - 20 + 80 - 30);

        // 对手 30002：只有玩法流水，手动/审批列全 0（来源互不串味）
        JsonNode other = memberRow("30002");
        assertThat(other.get("game_income").asLong()).isEqualTo(25);
        assertThat(other.get("game_outcome").asLong()).isEqualTo(80);
        assertThat(other.get("manual_income").asLong()).isZero();
        assertThat(other.get("approve_income").asLong()).isZero();
        assertThat(other.get("points").asLong()).isEqualTo(1000 - 80 + 25);

        // 无任何流水：来源列全 0（不返回 null，前端直接展示 0）
        JsonNode empty = memberRow("30003");
        for (String k : List.of("manual_income", "manual_outcome", "approve_income", "approve_outcome",
                "game_income", "game_outcome", "game_flow")) {
            assertThat(empty.get(k).asLong()).as(k).isZero();
        }
    }

    // ========== 流水接口：summary + 来源过滤 ==========

    @Test
    void recordsSummaryIsFullScopeAndSourceFilterWorks() throws Exception {
        adminAdjust(100, "充值");
        openAdjust("/api/open/points/up", 50, "approve", "approve:rec-1");
        openAdjust("/api/open/points/down", 20, "approve", "approve:rec-2");
        reportRound("R-SRC-4", "[{\"qq\":\"30001\",\"nickname\":\"张三\",\"delta\":80,\"flow\":40},"
                + "{\"qq\":\"30002\",\"nickname\":\"李四\",\"delta\":-80,\"flow\":40}]");

        JsonNode all = records("30001", null);
        // 合计卡：手动/审批各自上下分、玩法得分失分与玩法流水额
        JsonNode s = all.get("summary");
        assertThat(s.get("manual_income").asLong()).isEqualTo(100);
        assertThat(s.get("manual_outcome").asLong()).isZero();
        assertThat(s.get("approve_income").asLong()).isEqualTo(50);
        assertThat(s.get("approve_outcome").asLong()).isEqualTo(20);
        assertThat(s.get("game_income").asLong()).isEqualTo(80);
        assertThat(s.get("game_outcome").asLong()).isZero();
        assertThat(s.get("game_flow").asLong()).isEqualTo(40);
        assertThat(all.get("total").asInt()).isEqualTo(4);
        // 列表行带 source（前端按「来源 × 收支」细分标签）
        for (JsonNode r : all.get("list")) {
            assertThat(r.get("source").asText()).isIn("manual", "approve", "game");
        }

        JsonNode manual = records("30001", "manual");
        assertThat(manual.get("total").asInt()).isEqualTo(1);
        JsonNode approve = records("30001", "approve");
        assertThat(approve.get("total").asInt()).isEqualTo(2);
        JsonNode game = records("30001", "game");
        assertThat(game.get("total").asInt()).isEqualTo(1);
        // summary 是全量口径，不随筛选变化
        assertThat(game.get("summary").get("approve_income").asLong()).isEqualTo(50);
        assertThat(game.get("summary").get("manual_income").asLong()).isEqualTo(100);
    }

    // ========== 报表口径：改读 source，不看 reason 文案 ==========

    @Test
    void reportSplitsBySourceNotByReasonText() throws Exception {
        // 文案像玩法、来源是手动 → 报表必须算手动
        jdbc.update("INSERT INTO point_records (qq, member_id, delta, flow_amount, type, reason, operator, biz_no, source, created_at)"
                        + " VALUES ('30001',?,60,60,'INCOME','玩法:伪造结算','admin','fake-1','manual',?)",
                memberId, now());
        // 文案像手动、来源是玩法 → 报表必须算玩法
        jdbc.update("INSERT INTO point_records (qq, member_id, delta, flow_amount, type, reason, operator, biz_no, source, created_at)"
                        + " VALUES ('30001',?,-20,-20,'OUTCOME','充值','admin','fake-2','game',?)",
                memberId, now());

        JsonNode today = overview().get("today");
        assertThat(today.get("up").asLong()).isEqualTo(60);         // 手动上分（含文案伪玩法那行）
        assertThat(today.get("down").asLong()).isZero();
        assertThat(today.get("manual_count").asLong()).isEqualTo(1);
        assertThat(today.get("game_in").asLong()).isZero();
        assertThat(today.get("game_out").asLong()).isEqualTo(20);   // 玩法失分（含文案伪手动那行）
        assertThat(today.get("game_flow_count").asLong()).isEqualTo(1);

        // 排行同样按来源分流：手动榜只看非 game，玩法榜只看 game
        JsonNode rankings = overview().get("rankings");
        assertThat(rankings.get("up_today").get(0).get("qq").asText()).isEqualTo("30001");
        assertThat(rankings.get("up_today").get(0).get("amount").asLong()).isEqualTo(60);
        assertThat(rankings.get("down_today")).isEmpty();                    // 无手动下分
        assertThat(rankings.get("win_today")).isEmpty();                     // 无玩法得分
        assertThat(rankings.get("lose_today").get(0).get("amount").asLong()).isEqualTo(20);
    }

    // ========== 辅助 ==========

    private void adminAdjust(long delta, String reason) throws Exception {
        mvc.perform(post("/api/admin/members/" + memberId + "/points")
                        .header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"delta\":" + delta + ",\"reason\":\"" + reason + "\"}"))
                .andExpect(status().isOk());
    }

    private void openAdjust(String path, long points, String source, String bizNo) throws Exception {
        String body = "{\"executor_token\":\"" + exeToken + "\",\"qq\":\"30001\",\"points\":" + points
                + ",\"reason\":\"审批\",\"bizNo\":\"" + bizNo + "\""
                + (source == null ? "" : ",\"source\":\"" + source + "\"") + "}";
        mvc.perform(post(path).header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isOk());
    }

    private void reportRound(String roundId, String events) throws Exception {
        mvc.perform(post("/api/open/games/report")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"executor_token\":\"" + exeToken + "\",\"round_id\":\"" + roundId + "\","
                                + "\"play_id\":\"1\",\"play_name\":\"复合玩法\",\"group_id\":\"78002\","
                                + "\"events\":" + events + "}"))
                .andExpect(status().isOk());
    }

    private JsonNode memberRow(String qq) throws Exception {
        MvcResult r = mvc.perform(get("/api/admin/members").header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isOk()).andReturn();
        for (JsonNode row : om.readTree(r.getResponse().getContentAsString()).get("data")) {
            if (qq.equals(row.get("qq").asText())) return row;
        }
        throw new AssertionError("会员不存在: " + qq);
    }

    private JsonNode records(String qq, String source) throws Exception {
        MvcResult r = mvc.perform(get("/api/admin/point-records")
                        .param("qq", qq).param("page", "1").param("size", "20")
                        .param("source", source == null ? "" : source)
                        .header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isOk()).andReturn();
        return om.readTree(r.getResponse().getContentAsString()).get("data");
    }

    private JsonNode overview() throws Exception {
        MvcResult r = mvc.perform(get("/api/admin/report/overview").header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isOk()).andReturn();
        return om.readTree(r.getResponse().getContentAsString()).get("data");
    }

    private int recordCount(String where) {
        Integer c = jdbc.queryForObject("SELECT COUNT(*) FROM point_records WHERE " + where, Integer.class);
        return c == null ? 0 : c;
    }

    /** 取 30001 该变动值对应的唯一流水行（列名小写，与 RowMapMapper 一致） */
    private Map<String, Object> record(long delta) {
        List<Map<String, Object>> rows = jdbc.query(
                "SELECT delta, flow_amount, type, reason, operator, source FROM point_records"
                        + " WHERE qq='30001' AND delta=?", new com.hbjf.api.dao.RowMapMapper(), delta);
        assertThat(rows).as("delta=%s 的流水应恰好一条", delta).hasSize(1);
        return rows.get(0);
    }

    private String now() {
        return java.time.LocalDateTime.now()
                .format(java.time.format.DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss"));
    }
}
