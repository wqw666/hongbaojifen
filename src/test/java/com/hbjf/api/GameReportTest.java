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

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 游戏记录上报/回放入账测试：结算幂等、未知会员跳过、余额不足跳过、管理端列表+详情回放、封禁拒报
 */
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class GameReportTest {

    @Autowired
    MockMvc mvc;
    @Autowired
    JdbcTemplate jdbc;
    @Autowired
    ObjectMapper om;

    String adminToken;
    String exeToken;

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
                        .content("{\"name\":\"exe-game-1\"}"))
                .andExpect(status().isOk()).andReturn();
        exeToken = om.readTree(created.getResponse().getContentAsString()).get("data").get("token_plain").asText();

        // 心跳绑定操作员 91003
        mvc.perform(post("/api/open/executor/heartbeat")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"token\":\"" + exeToken + "\",\"admin_qq\":\"91003\",\"admin_nickname\":\"小C\"}"))
                .andExpect(status().isOk()).andReturn();

        // 建档会员 30001/30002（带 token → 记注册人）
        mvc.perform(post("/api/open/members/batch")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"executor_token\":\"" + exeToken + "\",\"members\":[{\"qq\":\"30001\",\"nickname\":\"张三\"},"
                                + "{\"qq\":\"30002\",\"nickname\":\"李四\"}]}"))
                .andExpect(status().isOk()).andReturn();
    }

    @Test
    void reportRoundSettlesAndSkipsBadEvents() throws Exception {
        // R1：30001 +10；30002 +5、-5、-1（余额不足跳过）；99999 未知跳过；30003 未知跳过
        mvc.perform(post("/api/open/games/report")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"executor_token\":\"" + exeToken + "\",\"round_id\":\"R1-20260903-001\","
                                + "\"play_id\":\"1\",\"play_name\":\"扫雷\",\"group_id\":\"70001\","
                                + "\"events\":["
                                + "{\"qq\":\"30001\",\"nickname\":\"张三\",\"msg\":\"开\",\"reply\":\"命中雷\",\"delta\":10},"
                                + "{\"qq\":\"30002\",\"nickname\":\"李四\",\"msg\":\"押5\",\"reply\":\"中\",\"delta\":5},"
                                + "{\"qq\":\"30002\",\"msg\":\"再押\",\"delta\":-5},"
                                + "{\"qq\":\"30002\",\"delta\":-1},"
                                + "{\"qq\":\"99999\",\"nickname\":\"幽灵\",\"delta\":5},"
                                + "{\"qq\":\"30003\",\"delta\":3}"
                                + "]}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.duplicate").value(false))
                .andExpect(jsonPath("$.data.member_count").value(2))       // 实际结算会员 30001/30002
                .andExpect(jsonPath("$.data.total_delta").value(10))       // +10+5-5
                .andExpect(jsonPath("$.data.event_count").value(6))
                .andExpect(jsonPath("$.data.warning_count").value(3));

        assertThat(jdbc.queryForObject("SELECT points FROM members WHERE qq='30001'", Integer.class)).isEqualTo(10);
        assertThat(jdbc.queryForObject("SELECT points FROM members WHERE qq='30002'", Integer.class)).isZero();

        // 流水：3 条入账（30001 一条、30002 两条），biz_no = game:{round_id}:{qq}:{事件序号}
        Integer bizCnt = jdbc.queryForObject(
                "SELECT COUNT(*) FROM point_records WHERE biz_no='game:R1-20260903-001:30001:1'", Integer.class);
        assertThat(bizCnt).isEqualTo(1);
        Integer prTotal = jdbc.queryForObject("SELECT COUNT(*) FROM point_records", Integer.class);
        assertThat(prTotal).isEqualTo(3);
        String reason = jdbc.queryForObject("SELECT reason FROM point_records WHERE qq='30001' ORDER BY id DESC",
                String.class);
        assertThat(reason).contains("玩法:扫雷");
        String operator = jdbc.queryForObject("SELECT operator FROM point_records WHERE qq='30001' ORDER BY id DESC",
                String.class);
        assertThat(operator).contains("executor:");
        // 事件不带 flow → 流水额 = delta（旧口径：谁下注谁记全额；+10+5-5）
        assertThat(jdbc.queryForObject("SELECT COALESCE(SUM(flow_amount),0) FROM point_records", Integer.class))
                .isEqualTo(10);

        // 局记录带 warning，能查到完整时间线
        Long recordId = jdbc.queryForObject("SELECT id FROM game_records WHERE round_id='R1-20260903-001'", Long.class);
        String warning = jdbc.queryForObject("SELECT warning FROM game_records WHERE id=?", String.class, recordId);
        assertThat(warning).contains("99999").contains("30003").contains("余额不足");
        assertThat(jdbc.queryForObject("SELECT operator_qq FROM game_records WHERE id=?", String.class, recordId))
                .isEqualTo("91003");
        assertThat(jdbc.queryForObject("SELECT executor_name FROM game_records WHERE id=?", String.class, recordId))
                .isEqualTo("exe-game-1");

        // 管理端列表 + 详情回放
        mvc.perform(get("/api/admin/game-records").header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.total").value(1))
                .andExpect(jsonPath("$.data.list[0].round_id").value("R1-20260903-001"));
        mvc.perform(get("/api/admin/game-records/" + recordId).header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.events.length()").value(6))
                .andExpect(jsonPath("$.data.events[0].qq").value("30001"))
                .andExpect(jsonPath("$.data.events[0].reply").value("命中雷"))
                .andExpect(jsonPath("$.data.events[4].qq").value("99999")); // 未入账玩家也留时间线
    }

    @Test
    void duplicateRoundIsIdempotent() throws Exception {
        String body = "{\"executor_token\":\"" + exeToken + "\",\"round_id\":\"R1-DUP\",\"play_name\":\"扫雷\","
                + "\"events\":[{\"qq\":\"30001\",\"delta\":7}]}";
        mvc.perform(post("/api/open/games/report").header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.total_delta").value(7));
        assertThat(jdbc.queryForObject("SELECT points FROM members WHERE qq='30001'", Integer.class)).isEqualTo(7);

        // 重放整局：duplicate=true，不重复入账
        mvc.perform(post("/api/open/games/report").header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.duplicate").value(true))
                .andExpect(jsonPath("$.data.total_delta").value(7));
        assertThat(jdbc.queryForObject("SELECT points FROM members WHERE qq='30001'", Integer.class)).isEqualTo(7);
        Integer recordCnt = jdbc.queryForObject("SELECT COUNT(*) FROM game_records WHERE round_id='R1-DUP'", Integer.class);
        assertThat(recordCnt).isEqualTo(1);
        Integer prCnt = jdbc.queryForObject("SELECT COUNT(*) FROM point_records", Integer.class);
        assertThat(prCnt).isEqualTo(1);

        // 坏 round_id / 缺 token / 空 events
        mvc.perform(post("/api/open/games/report").header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"round_id\":\"\",\"events\":[{\"qq\":\"30001\",\"delta\":1}]}"))
                .andExpect(status().isBadRequest());
        mvc.perform(post("/api/open/games/report").header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"round_id\":\"R-X\",\"events\":[{\"qq\":\"30001\",\"delta\":1}]}"))
                .andExpect(status().isBadRequest()).andExpect(jsonPath("$.code").value(40001));
        mvc.perform(post("/api/open/games/report").header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"executor_token\":\"" + exeToken + "\",\"round_id\":\"R-X\",\"events\":[]}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void bannedExecutorCannotReport() throws Exception {
        // 造一个已存在的真实对局
        mvc.perform(post("/api/open/games/report")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"executor_token\":\"" + exeToken + "\",\"round_id\":\"R1-BAN\",\"play_name\":\"扫雷\","
                                + "\"events\":[{\"qq\":\"30001\",\"delta\":2}]}"))
                .andExpect(status().isOk());

        long exeId = jdbc.queryForObject("SELECT id FROM executors WHERE token=?", Long.class, exeToken);
        mvc.perform(post("/api/admin/executors/" + exeId + "/ban")
                        .header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"reason\":\"上报异常\"}"))
                .andExpect(status().isOk());

        // 封禁后上报新局被拒，且未产生任何入账
        mvc.perform(post("/api/open/games/report")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"executor_token\":\"" + exeToken + "\",\"round_id\":\"R1-BAN2\",\"play_name\":\"扫雷\","
                                + "\"events\":[{\"qq\":\"30001\",\"delta\":99}]}"))
                .andExpect(status().isForbidden()).andExpect(jsonPath("$.code").value(40310));
        assertThat(jdbc.queryForObject("SELECT points FROM members WHERE qq='30001'", Integer.class)).isEqualTo(2);
        Integer cnt = jdbc.queryForObject("SELECT COUNT(*) FROM game_records", Integer.class);
        assertThat(cnt).isEqualTo(1);
    }

    @Test
    void listFiltersAndUnknownExecutorNameSnapshot() throws Exception {
        mvc.perform(post("/api/open/games/report")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"executor_token\":\"" + exeToken + "\",\"round_id\":\"R1-GRP-A\",\"play_name\":\"牛牛\","
                                + "\"group_id\":\"70001\",\"events\":[{\"qq\":\"30001\",\"delta\":1}]}"))
                .andExpect(status().isOk());

        // 按群过滤
        mvc.perform(get("/api/admin/game-records").param("group_id", "70001")
                        .header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.total").value(1))
                .andExpect(jsonPath("$.data.list[0].play_name").value("牛牛"));
        // 按不存在的群过滤 → 0
        mvc.perform(get("/api/admin/game-records").param("group_id", "99999")
                        .header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.total").value(0));
        // 未鉴权访问被拒
        mvc.perform(get("/api/admin/game-records"))
                .andExpect(status().isUnauthorized());
    }

    /**
     * 撑庄口径：积分按 delta（挑战者全额、庄家净额），流水按 flow（下注半额、恒正、两侧对等）；
     * 平局 delta=0 但 flow≠0 也记一条流水；群累计统计（局数/抽水）只在首次入账累加。
     */
    @Test
    void flowSplitsFromPointsAndGroupStatsAccumulate() throws Exception {
        // 本局：甲下注100赢(+80/流水50)、乙下注50平(0/流水25)、庄家净额(-90/流水75)
        // 积分面 Σdelta = -10 = -抽水；流水面 Σflow = 150 = 下注总额
        jdbc.update("INSERT INTO qq_groups (group_id, group_name, status, game_count, rake_total, created_at, updated_at)"
                + " VALUES ('70001','撑庄群','active',0,0,'2026-09-13 10:00:00','2026-09-13 10:00:00')");
        jdbc.update("UPDATE members SET points=100, group_id='70001' WHERE qq='30001'"); // 庄家需有余额可下
        jdbc.update("UPDATE members SET group_id='70001' WHERE qq='30002'");

        String body = "{\"executor_token\":\"" + exeToken + "\",\"round_id\":\"R1-QZZ-001\",\"play_name\":\"撑庄\","
                + "\"group_id\":\"70001\",\"events\":["
                + "{\"qq\":\"30001\",\"nickname\":\"张三\",\"delta\":80,\"flow\":50},"
                + "{\"qq\":\"30002\",\"nickname\":\"李四\",\"delta\":0,\"flow\":25},"
                + "{\"qq\":\"30001\",\"nickname\":\"张三\",\"delta\":-90,\"flow\":75}]}";
        mvc.perform(post("/api/open/games/report").header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.duplicate").value(false))
                .andExpect(jsonPath("$.data.total_delta").value(-10))
                .andExpect(jsonPath("$.data.member_count").value(2))
                .andExpect(jsonPath("$.data.warning_count").value(0));

        // 积分只按 delta 走：庄家 100+80-90=90，平局者不动
        assertThat(jdbc.queryForObject("SELECT points FROM members WHERE qq='30001'", Integer.class)).isEqualTo(90);
        assertThat(jdbc.queryForObject("SELECT points FROM members WHERE qq='30002'", Integer.class)).isZero();

        // 流水按 flow 落库
        assertThat(flowOf("game:R1-QZZ-001:30001:1")).isEqualTo(50);
        assertThat(flowOf("game:R1-QZZ-001:30001:3")).isEqualTo(75);
        Integer tieFlow = flowOf("game:R1-QZZ-001:30002:2");
        assertThat(tieFlow).isEqualTo(25);
        assertThat(jdbc.queryForObject("SELECT delta FROM point_records WHERE biz_no='game:R1-QZZ-001:30002:2'",
                Integer.class)).isZero();
        assertThat(jdbc.queryForObject("SELECT type FROM point_records WHERE biz_no='game:R1-QZZ-001:30002:2'",
                String.class)).isEqualTo("FLOW");
        // 回放时间线同样带流水额（平局行 delta=0/flow=25）
        assertThat(jdbc.queryForObject(
                "SELECT flow_amount FROM game_record_events WHERE record_id=(SELECT id FROM game_records"
                        + " WHERE round_id='R1-QZZ-001') AND qq='30002'", Integer.class)).isEqualTo(25);

        // 群累计统计：首次入账 game_count+1、rake_total += -total_delta
        assertThat(groupStat("game_count")).isEqualTo(1);
        assertThat(groupStat("rake_total")).isEqualTo(10);

        // 重复上报：幂等，不重复计数
        mvc.perform(post("/api/open/games/report").header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.duplicate").value(true));
        assertThat(groupStat("game_count")).isEqualTo(1);
        assertThat(groupStat("rake_total")).isEqualTo(10);

        // 游戏记录列表：总抽水跟随当前筛选
        mvc.perform(get("/api/admin/game-records").param("group_id", "70001")
                        .header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.total").value(1))
                .andExpect(jsonPath("$.data.rake_total").value(10));
        mvc.perform(get("/api/admin/game-records").param("group_id", "99999")
                        .header("Authorization", "Bearer " + adminToken))
                .andExpect(jsonPath("$.data.rake_total").value(0));

        // 群列表（管理端与 agent 开放接口同一读路径）：累计 + 近30天 + 群积分剩余
        mvc.perform(get("/api/admin/qq-groups").header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data[0].game_count").value(1))
                .andExpect(jsonPath("$.data[0].rake_total").value(10))
                .andExpect(jsonPath("$.data[0].game_count_30d").value(1))
                .andExpect(jsonPath("$.data[0].rake_total_30d").value(10))
                .andExpect(jsonPath("$.data[0].points_total").value(90));
        mvc.perform(get("/api/open/groups").header("X-Api-Key", "test-open-key"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data[0].game_count").value(1))
                .andExpect(jsonPath("$.data[0].rake_total_30d").value(10))
                .andExpect(jsonPath("$.data[0].points_total").value(90));

        // 会员列表按来源群过滤（群管理点群名看成员）
        mvc.perform(get("/api/admin/members").param("group_id", "70001")
                        .header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.length()").value(2));
        mvc.perform(get("/api/admin/members").param("group_id", "99999")
                        .header("Authorization", "Bearer " + adminToken))
                .andExpect(jsonPath("$.data.length()").value(0));
    }

    private Integer flowOf(String bizNo) {
        return jdbc.queryForObject("SELECT flow_amount FROM point_records WHERE biz_no=?", Integer.class, bizNo);
    }

    private Integer groupStat(String col) {
        return jdbc.queryForObject("SELECT " + col + " FROM qq_groups WHERE group_id='70001'", Integer.class);
    }
}
