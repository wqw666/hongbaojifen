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
}
