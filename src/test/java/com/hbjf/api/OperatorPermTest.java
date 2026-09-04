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
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 操作员权限门测试：总后台可改 状态/手动上下分权限；
 * denied=禁手动不禁自动（游戏结算照常）；disabled=该操作员名下全部带token写操作被拒
 */
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class OperatorPermTest {

    @Autowired
    MockMvc mvc;
    @Autowired
    JdbcTemplate jdbc;
    @Autowired
    ObjectMapper om;

    String adminToken;
    String exeToken;
    long operatorId;

    @BeforeEach
    void setUp() throws Exception {
        jdbc.update("DELETE FROM executor_commands");
        jdbc.update("DELETE FROM executors");
        jdbc.update("TRUNCATE TABLE game_record_events");
        jdbc.update("TRUNCATE TABLE game_records");
        jdbc.update("TRUNCATE TABLE point_records");
        jdbc.update("TRUNCATE TABLE members");
        jdbc.update("TRUNCATE TABLE qq_groups");
        jdbc.update("TRUNCATE TABLE qq_accounts");

        MvcResult login = mvc.perform(post("/api/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"admin\",\"password\":\"admin123\"}"))
                .andExpect(status().isOk()).andReturn();
        adminToken = om.readTree(login.getResponse().getContentAsString()).get("data").get("token").asText();

        MvcResult created = mvc.perform(post("/api/admin/executors")
                        .header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"exe-perm-1\"}"))
                .andExpect(status().isOk()).andReturn();
        exeToken = om.readTree(created.getResponse().getContentAsString()).get("data").get("token_plain").asText();

        // 心跳自动注册操作员 91002
        mvc.perform(post("/api/open/executor/heartbeat")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"token\":\"" + exeToken + "\",\"admin_qq\":\"91002\",\"admin_nickname\":\"小A\","
                                + "\"host\":\"host-a\",\"version\":\"1.0\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0));
        operatorId = jdbc.queryForObject("SELECT id FROM qq_accounts WHERE qq='91002'", Long.class);
        assertThat(jdbc.queryForObject("SELECT remark FROM qq_accounts WHERE id=?", String.class, operatorId))
                .contains("agent 自动注册");
        assertThat(jdbc.queryForObject("SELECT can_manual_points FROM qq_accounts WHERE id=?",
                String.class, operatorId)).isEqualTo("allowed");
        assertThat(jdbc.queryForObject("SELECT last_host FROM qq_accounts WHERE id=?",
                String.class, operatorId)).isEqualTo("host-a");
    }

    private void updateOperator(String status, String perm) throws Exception {
        String nickname = jdbc.queryForObject("SELECT nickname FROM qq_accounts WHERE id=?", String.class, operatorId);
        mvc.perform(put("/api/admin/qq-accounts/" + operatorId)
                        .header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"nickname\":\"" + nickname + "\",\"remark\":\"\",\"status\":\"" + status
                                + "\",\"can_manual_points\":\"" + perm + "\"}"))
                .andExpect(status().isOk());
    }

    private org.springframework.test.web.servlet.ResultActions manualUp(String token, String qq) throws Exception {
        return mvc.perform(post("/api/open/points/up")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"qq\":\"" + qq + "\",\"points\":5,\"reason\":\"操作员手动上分\","
                                + "\"executor_token\":\"" + token + "\"}"));
    }

    @Test
    void manualDeniedButAutoSettlementAllowed() throws Exception {
        // 建一个会员
        mvc.perform(post("/api/open/members/batch")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"executor_token\":\"" + exeToken + "\",\"members\":[{\"qq\":\"30001\",\"nickname\":\"张三\"}]}"))
                .andExpect(status().isOk());
        String registrar = jdbc.queryForObject("SELECT registrar_qq FROM members WHERE qq='30001'", String.class);
        assertThat(registrar).isEqualTo("91002"); // 注册人 = 心跳绑定的管理员QQ

        // 总后台改权限：禁手动
        updateOperator("active", "denied");
        assertThat(jdbc.queryForObject("SELECT can_manual_points FROM qq_accounts WHERE id=?",
                String.class, operatorId)).isEqualTo("denied");

        // 带 token 手动上分 → 40300
        manualUp(exeToken, "30001").andExpect(status().isForbidden()).andExpect(jsonPath("$.code").value(40300));

        // 自动结算不受影响：游戏上报照常入账
        mvc.perform(post("/api/open/games/report")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"executor_token\":\"" + exeToken + "\",\"round_id\":\"R-P1\",\"play_name\":\"扫雷\","
                                + "\"events\":[{\"qq\":\"30001\",\"delta\":8,\"msg\":\"win\"}]}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.total_delta").value(8));
        assertThat(jdbc.queryForObject("SELECT points FROM members WHERE qq='30001'", Integer.class)).isEqualTo(8);

        // 不带 token 的历史开放语义不受影响
        mvc.perform(post("/api/open/points/up")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"qq\":\"30001\",\"points\":2,\"reason\":\"旧接口调用\"}"))
                .andExpect(status().isOk());

        // 改回允许 → 手动可上分
        updateOperator("active", "allowed");
        manualUp(exeToken, "30001").andExpect(status().isOk());
    }

    @Test
    void disabledOperatorRejectsAllWrites() throws Exception {
        updateOperator("disabled", "allowed");
        assertThat(jdbc.queryForObject("SELECT status FROM qq_accounts WHERE id=?",
                String.class, operatorId)).isEqualTo("disabled");

        // 带 token 的注册 → 40311
        mvc.perform(post("/api/open/members/batch")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"executor_token\":\"" + exeToken + "\",\"members\":[{\"qq\":\"30002\"}]}"))
                .andExpect(status().isForbidden()).andExpect(jsonPath("$.code").value(40311));

        // 游戏上报 → 40311
        mvc.perform(post("/api/open/games/report")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"executor_token\":\"" + exeToken + "\",\"round_id\":\"R-P2\","
                                + "\"events\":[{\"qq\":\"30002\",\"delta\":1}]}"))
                .andExpect(status().isForbidden()).andExpect(jsonPath("$.code").value(40311));

        // 无 admin_qq 上报时的手动上分拒绝（未绑定 → 400）
        MvcResult bare = mvc.perform(post("/api/admin/executors")
                        .header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"exe-nobind\"}"))
                .andExpect(status().isOk()).andReturn();
        String bareToken = om.readTree(bare.getResponse().getContentAsString()).get("data").get("token_plain").asText();
        manualUp(bareToken, "30002").andExpect(status().isBadRequest());

        // 管理端可看到操作员新列（权限/状态/登录信息）
        mvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders
                        .get("/api/admin/qq-accounts").param("qq", "91002")
                        .header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data[0].can_manual_points").value("allowed"))
                .andExpect(jsonPath("$.data[0].status").value("disabled"))
                .andExpect(jsonPath("$.data[0].last_login_at").isNotEmpty());
    }
}
