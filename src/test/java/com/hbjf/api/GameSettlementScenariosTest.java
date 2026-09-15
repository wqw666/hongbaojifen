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

import java.util.ArrayList;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 多场景牌局结算用例（8 局，全部走真实上报接口 POST /api/open/games/report）
 *
 * 剧本覆盖：撑庄三方/四方/平局、大吃小两方/三方（流水兜底）、未知会员跳过、余额不足跳过。
 *
 * 每局不变量（逐局断言）：
 *   1) Σ(该局 point_records.delta) = game_records.total_delta
 *   2) total_delta = −本局抽水；干净局（无 warning）抽水 ≥ 0
 *   3) 该局所有流水 source='game'（来源分离），reason='玩法:{玩法名} 结算'
 *   4) 群累计 game_count +1、rake_total += −total_delta
 *   5) 撑庄局：流水额按 flow 透传（半额），庄家侧流水 = 挑战者侧流水之和（两侧对等、各自恒正）
 *   6) 平局：delta=0 但下过注 → 落一条 type=FLOW 的流水行，不动积分、不破坏守恒
 *
 * 期望值凡能由剧本推出的（流水额合计、得分/失分），一律在测试里按剧本重算，
 * 避免手算错数把错误口径固化进断言；变化总额/人数/warning 数这类「剧本设定」才写死。
 */
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class GameSettlementScenariosTest {

    private static final String GID = "78001";

    @Autowired
    MockMvc mvc;
    @Autowired
    JdbcTemplate jdbc;
    @Autowired
    ObjectMapper om;

    String adminToken;
    String exeToken;
    final List<String> roundIds = new ArrayList<>();

    /**
     * 一局剧本
     * @param id         局号
     * @param events     上报事件 JSON
     * @param totalDelta 期望本局总变动（= 已入账事件 delta 之和）
     * @param members    期望 member_count（未知会员不入数；余额不足者仍计入，其事件在时间线可见）
     * @param warnings   期望 warning 数
     * @param skip       被跳过的 QQ（未知会员/余额不足，逗号分隔，空串=全部入账）——重算期望值时排除
     */
    private record Round(String id, String events, long totalDelta, int members, int warnings, String skip) {}

    private static final List<Round> SCRIPT = List.of(
            // 1 撑庄三方：庄家丙（流水 75 = 50+25），抽水 3
            new Round("R-M1", "[{\"qq\":\"61001\",\"nickname\":\"甲\",\"delta\":100,\"flow\":50},"
                    + "{\"qq\":\"61002\",\"nickname\":\"乙\",\"delta\":50,\"flow\":25},"
                    + "{\"qq\":\"61003\",\"nickname\":\"丙\",\"delta\":-153,\"flow\":75}]", -3, 3, 0, ""),
            // 2 撑庄三方：庄家丙（80 = 50+30），抽水 3
            new Round("R-M2", "[{\"qq\":\"61001\",\"nickname\":\"甲\",\"delta\":-100,\"flow\":50},"
                    + "{\"qq\":\"61002\",\"nickname\":\"乙\",\"delta\":-60,\"flow\":30},"
                    + "{\"qq\":\"61003\",\"nickname\":\"丙\",\"delta\":157,\"flow\":80}]", -3, 3, 0, ""),
            // 3 撑庄平局：甲 delta=0 但下过注 → FLOW 行；庄家丙（70 = 50+20），抽水 2
            new Round("R-M3", "[{\"qq\":\"61001\",\"nickname\":\"甲\",\"delta\":0,\"flow\":50},"
                    + "{\"qq\":\"61002\",\"nickname\":\"乙\",\"delta\":40,\"flow\":20},"
                    + "{\"qq\":\"61003\",\"nickname\":\"丙\",\"delta\":-42,\"flow\":70}]", -2, 3, 0, ""),
            // 4 撑庄四方：庄家丁（120 = 30+40+50），抽水 4
            new Round("R-M4", "[{\"qq\":\"61001\",\"nickname\":\"甲\",\"delta\":60,\"flow\":30},"
                    + "{\"qq\":\"61002\",\"nickname\":\"乙\",\"delta\":-80,\"flow\":40},"
                    + "{\"qq\":\"61003\",\"nickname\":\"丙\",\"delta\":-100,\"flow\":50},"
                    + "{\"qq\":\"61004\",\"nickname\":\"丁\",\"delta\":116,\"flow\":120}]", -4, 4, 0, ""),
            // 5 大吃小两方：不带 flow → 流水额按 delta 全额兜底，抽水 4
            new Round("R-M5", "[{\"qq\":\"61002\",\"nickname\":\"乙\",\"delta\":96},"
                    + "{\"qq\":\"61001\",\"nickname\":\"甲\",\"delta\":-100}]", -4, 2, 0, ""),
            // 6 大吃小三方，抽水 6
            new Round("R-M6", "[{\"qq\":\"61003\",\"nickname\":\"丙\",\"delta\":100},"
                    + "{\"qq\":\"61002\",\"nickname\":\"乙\",\"delta\":-6},"
                    + "{\"qq\":\"61001\",\"nickname\":\"甲\",\"delta\":-100}]", -6, 3, 0, ""),
            // 7 撑庄 + 未建档玩家：幽灵整条跳过（不入账、不入数，事件仍留痕），其余照常，抽水 10
            new Round("R-M7", "[{\"qq\":\"61001\",\"nickname\":\"甲\",\"delta\":20,\"flow\":10},"
                    + "{\"qq\":\"61002\",\"nickname\":\"乙\",\"delta\":-30,\"flow\":15},"
                    + "{\"qq\":\"99999\",\"nickname\":\"幽灵\",\"delta\":50,\"flow\":25}]", -10, 2, 1, "99999"),
            // 8 撑庄 + 余额不足：丁 下分超余额整条跳过（绝不把余额打成负数），其余三人照常，抽水 5
            new Round("R-M8", "[{\"qq\":\"61001\",\"nickname\":\"甲\",\"delta\":40,\"flow\":20},"
                    + "{\"qq\":\"61002\",\"nickname\":\"乙\",\"delta\":-25,\"flow\":12},"
                    + "{\"qq\":\"61003\",\"nickname\":\"丙\",\"delta\":-20,\"flow\":10},"
                    + "{\"qq\":\"61004\",\"nickname\":\"丁\",\"delta\":-99999,\"flow\":20}]", -5, 4, 1, "61004")
    );

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
        roundIds.clear();

        MvcResult login = mvc.perform(post("/api/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"admin\",\"password\":\"admin123\"}"))
                .andExpect(status().isOk()).andReturn();
        adminToken = om.readTree(login.getResponse().getContentAsString()).get("data").get("token").asText();

        MvcResult created = mvc.perform(post("/api/admin/executors")
                        .header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"exe-multi-1\"}"))
                .andExpect(status().isOk()).andReturn();
        exeToken = om.readTree(created.getResponse().getContentAsString()).get("data").get("token_plain").asText();

        mvc.perform(post("/api/open/executor/heartbeat")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"token\":\"" + exeToken + "\",\"admin_qq\":\"91003\",\"admin_nickname\":\"小C\"}"))
                .andExpect(status().isOk()).andReturn();

        mvc.perform(post("/api/open/members/batch")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"executor_token\":\"" + exeToken + "\",\"members\":["
                                + "{\"qq\":\"61001\",\"nickname\":\"甲\"},{\"qq\":\"61002\",\"nickname\":\"乙\"},"
                                + "{\"qq\":\"61003\",\"nickname\":\"丙\"},{\"qq\":\"61004\",\"nickname\":\"丁\"}]}"))
                .andExpect(status().isOk()).andReturn();

        // 每人充值 1000（直填余额：本用例只关心牌局结算；来源分离见 PointSourceSeparationTest）
        jdbc.update("UPDATE members SET points=1000, total_income=1000, group_id=?", GID);

        jdbc.update("INSERT INTO qq_groups (group_id, group_name, status, game_count, rake_total, created_at, updated_at)"
                        + " VALUES (?, '多局测试群', 'active', 0, 0, '', '')", GID);
    }

    @Test
    void eightScenariosKeepEveryRoundBalanced() throws Exception {
        long rakeSum = 0;

        for (Round r : SCRIPT) {
            roundIds.add(r.id());
            JsonNode data = report(r.id(), r.events());

            assertThat(data.get("duplicate").asBoolean()).as(r.id()).isFalse();
            assertThat(data.get("total_delta").asLong()).as(r.id() + " 总变动").isEqualTo(r.totalDelta());
            assertThat(data.get("member_count").asInt()).as(r.id() + " 结算人数").isEqualTo(r.members());
            assertThat(data.get("warning_count").asInt()).as(r.id() + " warning").isEqualTo(r.warnings());

            // 1) Σ(该局流水 delta) = total_delta；2) 抽水 = −total_delta；干净局抽水非负
            long sumDelta = longOf("SELECT COALESCE(SUM(delta),0) FROM point_records WHERE biz_no LIKE 'game:" + r.id() + ":%'");
            long flowSum = longOf("SELECT COALESCE(SUM(flow_amount),0) FROM point_records WHERE biz_no LIKE 'game:" + r.id() + ":%'");
            assertThat(sumDelta).as(r.id() + " Σdelta").isEqualTo(r.totalDelta());
            assertThat(flowSum).as(r.id() + " Σ流水额").isEqualTo(scriptFlowSum(r));
            if (r.warnings() == 0) {
                assertThat(-r.totalDelta()).as(r.id() + " 抽水应非负").isGreaterThanOrEqualTo(0);
            }
            rakeSum += -r.totalDelta();

            // 3) 该局流水全部 source=game，reason 形如「玩法:xx 结算」
            int badSource = intOf("SELECT COUNT(*) FROM point_records WHERE biz_no LIKE 'game:" + r.id()
                    + ":%' AND (COALESCE(source,'') <> 'game' OR reason NOT LIKE '玩法:%结算')");
            assertThat(badSource).as(r.id() + " 来源/原因文案").isZero();

            // 4) 群累计：每局 +1，rake_total += −total_delta
            assertThat(longOf("SELECT game_count FROM qq_groups WHERE group_id='" + GID + "'"))
                    .as(r.id() + " 群局数").isEqualTo(roundIds.size());
            assertThat(longOf("SELECT rake_total FROM qq_groups WHERE group_id='" + GID + "'"))
                    .as(r.id() + " 群累计抽水").isEqualTo(rakeSum);
        }

        // 抽水合计 37；充值 4000 − 抽水 = 群积分剩余（与 qq_groups.points_total 一致）
        assertThat(rakeSum).isEqualTo(37);
        assertThat(longOf("SELECT COALESCE(SUM(points),0) FROM members")).isEqualTo(4000 - rakeSum);

        JsonNode group = groupRow();
        assertThat(group.get("game_count").asLong()).isEqualTo(8);
        assertThat(group.get("rake_total").asLong()).isEqualTo(rakeSum);
        assertThat(group.get("points_total").asLong()).isEqualTo(4000 - rakeSum);
    }

    @Test
    void bankerAndChallengerFlowsAreTwoSided() throws Exception {
        // 撑庄四方局：庄家丁 120 = 30 + 40 + 50（两侧对等，各自恒正）
        report("R-M4", SCRIPT.get(3).events());

        long banker = flowOf("R-M4", "61004");
        long challengers = flowOf("R-M4", "61001") + flowOf("R-M4", "61002") + flowOf("R-M4", "61003");
        assertThat(banker).isEqualTo(challengers).isEqualTo(120);
        for (String qq : List.of("61001", "61002", "61003", "61004")) {
            assertThat(flowOf("R-M4", qq)).as(qq + " 流水恒正").isGreaterThan(0);
        }
        // 撑庄局：所有流水额都不等于 delta（半额口径，不是「谁下注谁记全额」）
        assertThat(intOf("SELECT COUNT(*) FROM point_records WHERE delta<>0 AND flow_amount=delta")).isZero();
    }

    @Test
    void drawRoundKeepsFlowWithoutTouchingPoints() throws Exception {
        // 平局局：甲 0/50 落 type=FLOW 行；乙 +40、丙 −42 正常动分
        report("R-M3", SCRIPT.get(2).events());

        assertThat(intOf("SELECT COUNT(*) FROM point_records WHERE type='FLOW' AND delta=0 AND flow_amount=50")).isEqualTo(1);
        assertThat(longOf("SELECT points FROM members WHERE qq='61001'")).isEqualTo(1000);   // 平局不动分
        assertThat(longOf("SELECT points FROM members WHERE qq='61002'")).isEqualTo(1040);
        assertThat(longOf("SELECT points FROM members WHERE qq='61003'")).isEqualTo(958);
        // 平局 FLOW 行是「只记流水不动分」的行，来源同样是 game
        assertThat(strOf("SELECT source FROM point_records WHERE type='FLOW'")).isEqualTo("game");
        // 整局 Σdelta = −抽水（FLOW 行 delta=0 不破坏守恒）
        assertThat(longOf("SELECT COALESCE(SUM(delta),0) FROM point_records")).isEqualTo(-2);
    }

    @Test
    void skippedPlayersDoNotBreakTheRestOrGoNegative() throws Exception {
        // 未知会员（局 7）+ 余额不足（局 8）
        report("R-M7", SCRIPT.get(6).events());
        report("R-M8", SCRIPT.get(7).events());

        // 幽灵未建档：无任何流水，也未建出会员
        assertThat(intOf("SELECT COUNT(*) FROM point_records WHERE qq='99999'")).isZero();
        assertThat(intOf("SELECT COUNT(*) FROM members WHERE qq='99999'")).isZero();
        // 丁 余额不足 → 整条跳过（不入账、不进流水），其余三人照常
        assertThat(intOf("SELECT COUNT(*) FROM point_records WHERE biz_no LIKE 'game:R-M8:61004:%'")).isZero();
        assertThat(longOf("SELECT points FROM members WHERE qq='61004'")).isEqualTo(1000);
        assertThat(longOf("SELECT points FROM members WHERE qq='61001'")).isEqualTo(1060);   // +20(局7) +40(局8)
        // 跳过者的尝试仍进事件时间线（回放可见），只是 delta/flow 记 0
        assertThat(intOf("SELECT COUNT(*) FROM game_record_events e INNER JOIN game_records r ON e.record_id=r.id"
                + " WHERE r.round_id='R-M8' AND e.qq='61004' AND e.delta=0 AND e.flow_amount=0")).isEqualTo(1);
        // 余额永不为负
        assertThat(intOf("SELECT COUNT(*) FROM members WHERE points < 0")).isZero();
        // 两局抽水 10 + 5 照常入群累计
        assertThat(longOf("SELECT rake_total FROM qq_groups WHERE group_id='" + GID + "'")).isEqualTo(15);
    }

    @Test
    void memberListAndReportShowGameSideBySideWithManual() throws Exception {
        // 结算前：玩法得/失分与手动上下分都是 0
        JsonNode before = memberRow("61001");
        assertThat(before.get("manual_income").asLong()).isZero();
        assertThat(before.get("manual_outcome").asLong()).isZero();
        assertThat(before.get("game_income").asLong()).isZero();
        assertThat(before.get("game_outcome").asLong()).isZero();

        long rakeSum = 0;
        for (Round r : SCRIPT) {
            roundIds.add(r.id());
            report(r.id(), r.events());
            rakeSum += -r.totalDelta();
        }

        // 会员列表：玩法得分/失分按剧本重算（被跳过者不计）
        for (String qq : List.of("61001", "61002", "61003", "61004")) {
            JsonNode row = memberRow(qq);
            assertThat(row.get("game_income").asLong()).as(qq + " 玩法得分").isEqualTo(scriptIncome(qq));
            assertThat(row.get("game_outcome").asLong()).as(qq + " 玩法失分").isEqualTo(scriptOutcome(qq));
            assertThat(row.get("manual_income").asLong()).as(qq + " 手动上分").isZero();
            assertThat(row.get("manual_outcome").asLong()).as(qq + " 手动下分").isZero();
        }

        // 报表：玩法得失分只认 game 来源；FLOW 行（只记流水不动分）不进 up/down
        JsonNode today = overview().get("today");
        assertThat(today.get("game_count").asLong()).isEqualTo(8);
        assertThat(today.get("fee").asLong()).isEqualTo(rakeSum);
        assertThat(today.get("up").asLong()).isZero();
        assertThat(today.get("down").asLong()).isZero();
        assertThat(today.get("game_in").asLong()).isEqualTo(scriptTotalIncome());
        assertThat(today.get("game_out").asLong()).isEqualTo(scriptTotalOutcome());

        // 群管理列表：局数/抽水/积分剩余三列同源一致
        JsonNode grp = groupRow();
        assertThat(grp.get("game_count").asLong()).isEqualTo(8);
        assertThat(grp.get("rake_total").asLong()).isEqualTo(rakeSum);
        assertThat(grp.get("points_total").asLong()).isEqualTo(4000 - rakeSum);
    }

    // ========== 剧本重算（测试侧独立实现，避免手算错数） ==========

    /** 该局期望流水额合计：事件带 flow 用 flow，不带按 delta 兜底；被跳过者不产生流水 */
    private long scriptFlowSum(Round r) throws Exception {
        long sum = 0;
        for (JsonNode e : om.readTree(r.events())) {
            if (r.skip().contains(e.get("qq").asText())) continue;
            sum += e.has("flow") ? e.get("flow").asLong() : e.get("delta").asLong();
        }
        return sum;
    }

    private long scriptIncome(String qq) throws Exception {
        return scriptSide(qq, true);
    }

    private long scriptOutcome(String qq) throws Exception {
        return scriptSide(qq, false);
    }

    private long scriptTotalIncome() throws Exception {
        return scriptTotalSide(true);
    }

    private long scriptTotalOutcome() throws Exception {
        return scriptTotalSide(false);
    }

    private long scriptSide(String qq, boolean positive) throws Exception {
        long sum = 0;
        for (Round r : SCRIPT) {
            if (r.skip().contains(qq)) continue;
            for (JsonNode e : om.readTree(r.events())) {
                if (!qq.equals(e.get("qq").asText())) continue;
                long d = e.get("delta").asLong();
                if (positive ? d > 0 : d < 0) sum += Math.abs(d);
            }
        }
        return sum;
    }

    private long scriptTotalSide(boolean positive) throws Exception {
        long sum = 0;
        for (Round r : SCRIPT) {
            for (JsonNode e : om.readTree(r.events())) {
                if (r.skip().contains(e.get("qq").asText())) continue;
                long d = e.get("delta").asLong();
                if (positive ? d > 0 : d < 0) sum += Math.abs(d);
            }
        }
        return sum;
    }

    // ========== 接口与查询辅助 ==========

    private JsonNode report(String roundId, String events) throws Exception {
        MvcResult r = mvc.perform(post("/api/open/games/report")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"executor_token\":\"" + exeToken + "\",\"round_id\":\"" + roundId + "\","
                                + "\"play_id\":\"1\",\"play_name\":\"复合玩法\",\"group_id\":\"" + GID + "\","
                                + "\"events\":" + events + "}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.round_id").value(roundId))
                .andReturn();
        return om.readTree(r.getResponse().getContentAsString()).get("data");
    }

    private JsonNode overview() throws Exception {
        MvcResult r = mvc.perform(get("/api/admin/report/overview").header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isOk()).andReturn();
        return om.readTree(r.getResponse().getContentAsString()).get("data");
    }

    private JsonNode memberRow(String qq) throws Exception {
        MvcResult r = mvc.perform(get("/api/admin/members").header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isOk()).andReturn();
        for (JsonNode row : om.readTree(r.getResponse().getContentAsString()).get("data")) {
            if (qq.equals(row.get("qq").asText())) return row;
        }
        throw new AssertionError("会员不存在: " + qq);
    }

    private JsonNode groupRow() throws Exception {
        MvcResult r = mvc.perform(get("/api/admin/qq-groups").header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isOk()).andReturn();
        for (JsonNode row : om.readTree(r.getResponse().getContentAsString()).get("data")) {
            if (GID.equals(row.get("group_id").asText())) return row;
        }
        throw new AssertionError("群不存在: " + GID);
    }

    /** 某局某人的流水额合计（撑庄半额；无独立流水则为 delta 兜底） */
    private long flowOf(String roundId, String qq) {
        return longOf("SELECT COALESCE(SUM(flow_amount),0) FROM point_records WHERE biz_no LIKE 'game:"
                + roundId + ":" + qq + ":%'");
    }

    private long longOf(String sql) {
        Long v = jdbc.queryForObject(sql, Long.class);
        return v == null ? 0 : v;
    }

    private int intOf(String sql) {
        Integer v = jdbc.queryForObject(sql, Integer.class);
        return v == null ? 0 : v;
    }

    private String strOf(String sql) {
        return jdbc.queryForObject(sql, String.class);
    }
}
